import os
import csv
import json
import math
import requests
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()
MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY")
STORMGLASS_API_KEY = os.getenv("STORMGLASS_API_KEY")

DB_PATH = "DB spots.csv"
CRENEAUX_UTC = [5, 8, 11, 14, 17, 19]  # → 7h 10h 13h 16h 19h 21h FR
POIDS = {"direction": 0.40, "periode": 0.25, "taille": 0.10}

# ─── Utilitaires géo ──────────────────────────────────────────

CARDINALS = ["N","NNE","NE","ENE","E","ESE","SE","SSE","S","SSW","SW","WSW","W","WNW","NW","NNW"]
CARDINAL_DEG = {c: i * 22.5 for i, c in enumerate(CARDINALS)}

def deg_to_cardinal(deg):
    if deg is None: return "—"
    return CARDINALS[round(deg / 22.5) % 16]

def cardinal_to_deg(s):
    return CARDINAL_DEG.get(s.upper() if s else "", None)

def angle_diff(a, b):
    d = abs(a - b) % 360
    return d if d <= 180 else 360 - d

def distance_km(lat1, lng1, lat2, lng2):
    R = 6371
    dlat, dlng = math.radians(lat2-lat1), math.radians(lng2-lng1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1))*math.cos(math.radians(lat2))*math.sin(dlng/2)**2
    return R * 2 * math.asin(math.sqrt(a))

def geocode(lieu):
    try:
        r = requests.get("https://nominatim.openstreetmap.org/search",
            params={"q": lieu, "format": "json", "limit": 1},
            headers={"User-Agent": "SurfAgent/1.0"}, timeout=5)
        res = r.json()
        if res: return float(res[0]["lat"]), float(res[0]["lon"])
    except: pass
    return None, None

# ─── Scoring ──────────────────────────────────────────────────

def direction_score(deg, direction_str):
    """Score 0-1 : à quel point la direction de houle matche l'idéal du spot."""
    if deg is None: return 0.5
    parts = direction_str.strip().split("-")
    if len(parts) == 1:
        d = cardinal_to_deg(parts[0])
        if d is None: return 0.5
        diff = angle_diff(deg, d)
        if diff <= 22.5: return 1.0
        elif diff <= 45: return 0.7
        elif diff <= 90: return 0.3
        return 0.0
    d1, d2 = cardinal_to_deg(parts[0]), cardinal_to_deg(parts[-1])
    if d1 is None or d2 is None: return 0.5
    span = angle_diff(d1, d2)
    center = (d1 + ((d2 - d1 + 180) % 360 - 180) / 2) % 360
    diff = angle_diff(deg, center)
    if diff <= span/2: return 1.0
    elif diff <= span/2 + 22.5: return 0.7
    elif diff <= span/2 + 45: return 0.3
    return 0.0

def periode_score(p):
    if p is None: return 0.5
    if p < 8: return 0.2
    elif p < 10: return 0.5
    elif p < 12: return 0.7
    elif p < 14: return 0.9
    return 1.0

def vent_categorie(wind_deg, ideal_str):
    """Retourne la categorie de vent : offshore, cross-off, cross-on, onshore."""
    if wind_deg is None: return "inconnu"
    ideal = cardinal_to_deg(ideal_str.split("-")[0])
    if ideal is None: return "inconnu"
    diff = angle_diff(wind_deg, ideal)
    if diff <= 45:   return "offshore"
    elif diff <= 90: return "cross-off"
    elif diff <= 135: return "cross-on"
    else:            return "onshore"

def vent_range(wind_kmh, categorie):
    """Retourne le range (min, max) selon la force et la categorie du vent."""
    if wind_kmh is None: return (0, 10)
    if wind_kmh < 7:
        return (0, 10)
    elif wind_kmh < 15:
        return {"offshore": (6, 10), "cross-off": (4, 9),
                "cross-on": (1, 6),  "onshore":   (0, 4)}.get(categorie, (0, 10))
    elif wind_kmh < 25:
        return {"offshore": (4, 9),  "cross-off": (2, 7),
                "cross-on": (0, 3),  "onshore":   (0, 2)}.get(categorie, (0, 10))
    else:
        return {"offshore": (3, 8),  "cross-off": (1, 5),
                "cross-on": (0, 2),  "onshore":   None}.get(categorie, (0, 10))

def vent_qualite(wind_deg, ideal_str):
    cat = vent_categorie(wind_deg, ideal_str)
    labels = {"offshore": "offshore ✓", "cross-off": "cross-off ~",
              "cross-on": "cross-on ~", "onshore": "onshore ✗", "inconnu": "inconnu"}
    return labels.get(cat, "inconnu")

def taille_score(h, tmin, tmax):
    if h is None: return 0.5
    if h < tmin: return max(0, h/tmin)
    elif h <= tmax: return 1.0
    return 0.0

def get_val(h, param):
    src = h.get(param, {})
    v = src.get("sg") or src.get("noaa") or src.get("meto")
    return round(v, 2) if v is not None else None

def scorer(spot, h):
    """
    Prend un spot (dict DB) et une heure Stormglass.
    Retourne (info_dict, None) si surfable, (None, raison_str) si éliminé.
    Le vent definit un range dans lequel le score brut (houle+periode+taille) est mappe.
    """
    swell_h   = get_val(h, "swellHeight")
    swell_dir = get_val(h, "swellDirection")
    swell_per = get_val(h, "swellPeriod")
    wind_ms   = get_val(h, "windSpeed")
    wind_dir  = get_val(h, "windDirection")
    wind_kmh  = round(wind_ms * 3.6, 1) if wind_ms else None

    # Filtres éliminatoires
    if swell_h and swell_h > spot["houle_max"]:
        return None, f"houle trop grosse ({swell_h}m > max {spot['houle_max']}m)"

    cat = vent_categorie(wind_dir, spot["vent_ideal"])
    rng = vent_range(wind_kmh, cat)

    # Vent onshore fort = éliminatoire
    if rng is None:
        return None, f"vent fort onshore ({wind_kmh}km/h {deg_to_cardinal(wind_dir)})"

    # Score brut houle + periode + taille (renormalisé sur leurs poids)
    poids_sans_vent = POIDS["direction"] + POIDS["periode"] + POIDS["taille"]
    score_brut = (
        direction_score(swell_dir, spot["houle_dir"]) * POIDS["direction"] +
        periode_score(swell_per)                      * POIDS["periode"] +
        taille_score(swell_h, spot["houle_min"], spot["houle_max"]) * POIDS["taille"]
    ) / poids_sans_vent  # → valeur entre 0 et 1

    # Mapping dans le range defini par le vent
    rng_min, rng_max = rng
    score = round(rng_min + score_brut * (rng_max - rng_min), 1)

    return {
        "score":        score,
        "houle":        f"{swell_h}m" if swell_h else "—",
        "dir_houle":    deg_to_cardinal(swell_dir),
        "dir_ideale":   spot["houle_dir"],
        "periode":      f"{swell_per}s" if swell_per else "—",
        "vent":         f"{wind_kmh}km/h {deg_to_cardinal(wind_dir)}" if wind_kmh else "—",
        "vent_ideal":   spot["vent_ideal"],
        "vent_qualite": vent_qualite(wind_dir, spot["vent_ideal"]),
        "vent_cat":     cat,
        "vent_kmh":     wind_kmh,
        "maree":        spot["maree_ideale"],
        "type_vague":   spot["type_vague"],
        "crowd":        spot["crowd"],
    }, None

# ─── DB ───────────────────────────────────────────────────────

def charger_spots():
    with open(DB_PATH, newline='', encoding='utf-8') as f:
        return [{
            "nom":        r["nom"],
            "region":     r["region"],
            "lat":        float(r["lat"]),
            "lng":        float(r["lng"]),
            "houle_dir":  r["houle_direction"],
            "houle_min":  float(r["houle_taille_min_m"]),
            "houle_max":  float(r["houle_taille_max_m"]),
            "vent_ideal": r["vent_direction_ideal"],
            "maree_ideale": r["maree_ideale"],
            "type_break": r["type_break"],
            "type_vague": r["type_vague"],
            "crowd":      r["crowd"],
        } for r in csv.DictReader(f)]

# ─── Fetch Stormglass ─────────────────────────────────────────

def fetch_stormglass(lat, lng):
    r = requests.get("https://api.stormglass.io/v2/weather/point",
        params={"lat": lat, "lng": lng,
                "params": "swellHeight,swellDirection,swellPeriod,windSpeed,windDirection"},
        headers={"Authorization": STORMGLASS_API_KEY}, timeout=10)
    return r.json() if r.status_code == 200 else None

# ─── Outils agent ─────────────────────────────────────────────

def _format_classement(resultats, elimines):
    """Formate un classement en string lisible pour le LLM."""
    from collections import defaultdict
    # Meilleur créneau par spot par jour
    meilleurs = {}
    for r in resultats:
        key = (r["nom"], r["creneau"].split(" ")[0])
        if key not in meilleurs or r["score"] > meilleurs[key]["score"]:
            meilleurs[key] = r

    par_date = defaultdict(list)
    for r in sorted(meilleurs.values(), key=lambda x: (x["creneau"], -x["score"])):
        par_date[r["creneau"].split(" ")[0]].append(r)

    lines = []
    for date in sorted(par_date):
        lines.append(f"\n=== {date} ===")
        for i, r in enumerate(sorted(par_date[date], key=lambda x: -x["score"]), 1):
            lines.append(
                f"{i}. {r['nom']} ({r['region']}) [{r['creneau']}] — Score: {r['score']}/10 | "
                f"Houle: {r['houle']} {r['dir_houle']} (idéal: {r['dir_ideale']}) | "
                f"Période: {r['periode']} | Vent: {r['vent']} (idéal: {r['vent_ideal']}, {r['vent_qualite']}) | "
                f"Marée: {r['maree']} | Vague: {r['type_vague']} | Crowd: {r['crowd']}")
    if elimines:
        lines.append(f"\nÉliminés : {', '.join(set(e['nom'] for e in elimines))}")
    return "\n".join(lines) or "Aucun résultat."

def _iter_spot_creneaux(spot, data, cible_utc=None):
    """Générateur : yield (label_fr, info_or_None, raison_or_None) pour chaque créneau."""
    for h in data.get("hours", []):
        try:
            dt = datetime.fromisoformat(h["time"].replace("Z", "+00:00"))
            if cible_utc is not None:
                if dt.hour != cible_utc: continue
            elif dt.hour not in CRENEAUX_UTC: continue
            heure_fr = (dt.hour + 2) % 24
            label = f"{dt.strftime('%d/%m')} {heure_fr:02d}h"
            info, raison = scorer(spot, h)
            yield label, info, raison
            if cible_utc is not None: break
        except: continue

def tool_get_classement(region="", creneau=""):
    """Classement de tous les spots (ou d'une région) sur tous les créneaux."""
    from forecast import get_or_fetch_all  # import local pour éviter circularité
    all_data = get_or_fetch_all()

    spots = charger_spots()
    if region:
        spots = [s for s in spots if region.lower() in s["region"].lower()]

    cible_utc = None
    if creneau:
        try: cible_utc = (int(creneau.replace("h","")) - 2) % 24
        except: pass

    resultats, elimines = [], []
    for spot in spots:
        data = all_data.get(spot["nom"])
        if not data: continue
        for label, info, raison in _iter_spot_creneaux(spot, data, cible_utc):
            if info: resultats.append({"nom": spot["nom"], "region": spot["region"], "creneau": label, **info})
            else: elimines.append({"nom": spot["nom"], "raison": raison, "creneau": label})

    return _format_classement(resultats, elimines)

def tool_get_spot(nom_spot, creneau=""):
    """Conditions détaillées d'un spot précis."""
    from forecast import get_or_fetch_spot
    spots = charger_spots()
    spot = next((s for s in spots if nom_spot.lower() in s["nom"].lower()), None)
    if not spot: return f"Spot '{nom_spot}' non trouvé dans la DB."

    data = get_or_fetch_spot(spot)
    if not data: return "Erreur fetch météo."

    cible_utc = None
    if creneau:
        try: cible_utc = (int(creneau.replace("h","")) - 2) % 24
        except: pass

    header = (f"Spot : {spot['nom']} ({spot['region']})\n"
              f"\n--- DB (conditions idéales) ---\n"
              f"Houle : {spot['houle_dir']} | {spot['houle_min']}-{spot['houle_max']}m\n"
              f"Vent  : {spot['vent_ideal']} | Marée : {spot['maree_ideale']}\n"
              f"Break : {spot['type_break']} | Vague : {spot['type_vague']} | Crowd : {spot['crowd']}\n"
              f"\n--- Forecast ---\n")

    lines = []
    for label, info, raison in _iter_spot_creneaux(spot, data, cible_utc):
        if info:
            lines.append(f"{label} — Score: {info['score']}/10 | Houle: {info['houle']} {info['dir_houle']} | "
                         f"Période: {info['periode']} | Vent: {info['vent']} ({info['vent_qualite']})")
        else:
            lines.append(f"{label} — ÉLIMINÉ : {raison}")

    return header + ("\n".join(lines) if lines else "Aucune donnée.")

def tool_get_spots_near(lieu, rayon_km=60):
    """Spots proches d'un lieu avec leurs conditions."""
    from forecast import get_or_fetch_spot
    lat, lng = geocode(lieu)
    if lat is None: return f"Impossible de localiser '{lieu}'."

    spots = sorted(
        [(distance_km(lat, lng, s["lat"], s["lng"]), s) for s in charger_spots()],
        key=lambda x: x[0]
    )
    spots = [(d, s) for d, s in spots if d <= rayon_km]
    if not spots: return f"Aucun spot à moins de {rayon_km}km de {lieu}."

    lines = [f"Spots à moins de {rayon_km}km de {lieu}:\n"]
    for dist, spot in spots:
        data = get_or_fetch_spot(spot)
        if not data: continue
        for label, info, raison in _iter_spot_creneaux(spot, data):
            if info:
                lines.append(f"• {spot['nom']} ({dist:.0f}km) [{label}] — Score: {info['score']}/10 | "
                    f"Houle: {info['houle']} {info['dir_houle']} (idéal: {info['dir_ideale']}) | "
                    f"Période: {info['periode']} | Vent: {info['vent']} ({info['vent_qualite']}) | "
                    f"Vague: {spot['type_vague']} | Crowd: {spot['crowd']}")
            else:
                lines.append(f"• {spot['nom']} ({dist:.0f}km) — ÉLIMINÉ: {raison}")
            break
    return "\n".join(lines)

# ─── Agent Mistral ────────────────────────────────────────────

TOOLS_SCHEMA = [
    {"type": "function", "function": {
        "name": "get_classement",
        "description": "Classement scoré des spots. creneau='' pour tous les créneaux disponibles.",
        "parameters": {"type": "object", "properties": {
            "region":  {"type": "string", "description": "Ex: 'Pays Basque', 'Asturies', '' pour tout"},
            "creneau": {"type": "string", "description": "Ex: '10h', '' pour tous"}
        }, "required": []}}},
    {"type": "function", "function": {
        "name": "get_spot",
        "description": "Conditions détaillées d'un spot avec données DB complètes.",
        "parameters": {"type": "object", "properties": {
            "nom_spot": {"type": "string", "description": "Ex: 'Mundaka', 'Hossegor'"},
            "creneau":  {"type": "string", "description": "Ex: '10h', '' pour tous"}
        }, "required": ["nom_spot"]}}},
    {"type": "function", "function": {
        "name": "get_spots_near",
        "description": "Spots proches d'une ville avec conditions. Utiliser pour toute mention de localisation.",
        "parameters": {"type": "object", "properties": {
            "lieu":      {"type": "string", "description": "Ex: 'Hendaye', 'San Sebastian'"},
            "rayon_km":  {"type": "number",  "description": "Rayon en km (défaut 60)"}
        }, "required": ["lieu"]}}},
]

TOOL_FUNCTIONS = {
    "get_classement":  tool_get_classement,
    "get_spot":        tool_get_spot,
    "get_spots_near":  tool_get_spots_near,
}

def get_system_prompt():
    from datetime import timedelta
    today = datetime.now()
    saturday = today + timedelta(days=(5 - today.weekday()) % 7)
    sunday = saturday + timedelta(days=1)
    return f"""Tu es SurfAgent, expert surf côte atlantique française et espagnole. 25 spots en DB.

## Outils
- get_classement(region, creneau) : classement scoré. creneau='' pour toute la fenêtre.
- get_spot(nom_spot, creneau) : données complètes d'un spot (DB + forecast).
- get_spots_near(lieu, rayon_km) : spots proches d'une localisation avec conditions.

## Règle d'or
Appelle toujours un outil avant de répondre. Ne jamais extrapoler sans données fraîches.

## Routing
- Localisation mentionnée → get_spots_near
- Spot précis → get_spot
- Tout le reste → get_classement

## Données
Les prévisions couvrent aujourd'hui + 10 jours. Ne déclare jamais une date indisponible sans avoir appelé l'outil et vérifié dans les résultats.

## Format
- Langue : celle de l'utilisateur. Termes surf toujours en anglais.
- Vent : TOUJOURS magnitude + direction + qualité. Ex : "5km/h NW (onshore ✗)".
- La qualité vent est pré-calculée dans vent_qualite — ne jamais la recalculer.
- Période — échelle absolue :
  * < 8s → Mauvais (windsurf/chop)
  * 8-10s → Passable
  * 10-12s → Correct
  * 12-14s → Bon
  * > 14s → Excellent
  Qualifier en absolu ET relatif si c'est le meilleur dispo. Ex : "7.8s (mauvais, meilleur ce jour)"
- Recommandation : score + conditions clés + pourquoi ce spot. Top 3 si plusieurs bons.
- Interdit : température eau, conseils matériel, tout ce qui n'est pas dans les données.
- Si les conditions demandées ne sont pas disponibles dans les données, le dire clairement et proposer le meilleur disponible en précisant l'écart.

## Date
Aujourd'hui : {today.strftime('%A %d/%m/%Y')}
Ce weekend : sam {saturday.strftime('%d/%m')} et dim {sunday.strftime('%d/%m')}
"""

def invoke(user_message):
    messages = [
        {"role": "system", "content": get_system_prompt()},
        {"role": "user",   "content": user_message}
    ]
    headers = {"Authorization": f"Bearer {MISTRAL_API_KEY}", "Content-Type": "application/json"}

    for _ in range(8):
        r = requests.post("https://api.mistral.ai/v1/chat/completions", headers=headers,
            json={"model": "mistral-small-latest", "messages": messages,
                  "tools": TOOLS_SCHEMA, "tool_choice": "auto", "temperature": 0.1},
            timeout=30)
        if r.status_code != 200:
            return f"Erreur Mistral {r.status_code}"

        msg = r.json()["choices"][0]["message"]
        messages.append(msg)

        if not msg.get("tool_calls"):
            return msg.get("content", "Pas de réponse.")

        for tc in msg["tool_calls"]:
            fn = TOOL_FUNCTIONS.get(tc["function"]["name"])
            args = json.loads(tc["function"]["arguments"])
            result = fn(**args) if fn else f"Outil inconnu: {tc['function']['name']}"
            messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result})

    return "Trop d'itérations — réessaie."
