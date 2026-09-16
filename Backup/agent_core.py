import os
import csv
import json
import requests
import math
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()
MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY")
STORMGLASS_API_KEY = os.getenv("STORMGLASS_API_KEY")

DB_PATH = "DB spots.csv"
CRENEAUX_UTC = [5, 8, 11, 14, 17, 19]

# ─── Utilitaires ──────────────────────────────────────────────

def deg_to_cardinal(deg):
    if deg is None: return "—"
    directions = ["N","NNE","NE","ENE","E","ESE","SE","SSE",
                  "S","SSW","SW","WSW","W","WNW","NW","NNW"]
    return directions[round(deg / 22.5) % 16]

def cardinal_to_deg(cardinal):
    mapping = {
        "N":0,"NNE":22.5,"NE":45,"ENE":67.5,"E":90,"ESE":112.5,
        "SE":135,"SSE":157.5,"S":180,"SSW":202.5,"SW":225,"WSW":247.5,
        "W":270,"WNW":292.5,"NW":315,"NNW":337.5,
    }
    return mapping.get(cardinal.upper() if cardinal else "", None)

def angle_diff(a, b):
    diff = abs(a - b) % 360
    return diff if diff <= 180 else 360 - diff

def geocode(lieu):
    try:
        r = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": lieu, "format": "json", "limit": 1},
            headers={"User-Agent": "SurfAgent/1.0"},
            timeout=5
        )
        results = r.json()
        if results:
            return float(results[0]["lat"]), float(results[0]["lon"])
    except:
        pass
    return None, None

def distance_km(lat1, lng1, lat2, lng2):
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng/2)**2
    return R * 2 * math.asin(math.sqrt(a))

def direction_in_range(deg_forecast, direction_str):
    if deg_forecast is None: return 0.5
    parts = direction_str.strip().split("-")
    if len(parts) == 1:
        d = cardinal_to_deg(parts[0])
        if d is None: return 0.5
        diff = angle_diff(deg_forecast, d)
        if diff <= 22.5: return 1.0
        elif diff <= 45: return 0.7
        elif diff <= 90: return 0.3
        else: return 0.0
    else:
        d1 = cardinal_to_deg(parts[0])
        d2 = cardinal_to_deg(parts[-1])
        if d1 is None or d2 is None: return 0.5
        # Chemin le plus court entre d1 et d2 (toujours <= 180°)
        # Ex: N-W → chemin court = N→NNW→NW→WNW→W = 90° (pas 270° en passant par S)
        span = angle_diff(d1, d2)  # toujours <= 180°
        center = d1 + ((d2 - d1 + 180) % 360 - 180) / 2  # milieu du chemin court
        center = center % 360
        dist_center = angle_diff(deg_forecast, center)
        half = span / 2
        if dist_center <= half:
            return 1.0  # dans la plage
        elif dist_center <= half + 22.5:
            return 0.7
        elif dist_center <= half + 45:
            return 0.3
        else:
            return 0.0

def score_periode(p):
    if p is None: return 0.5
    if p < 8: return 0.2
    elif p < 10: return 0.5
    elif p < 12: return 0.7
    elif p < 14: return 0.9
    else: return 1.0

def score_taille(h, tmin, tmax):
    if h is None: return 0.5
    if h < tmin: return max(0, h / tmin)
    elif h <= tmax: return 1.0
    else: return 0.0

def score_vent(wind_kmh, wind_deg, vent_ideal_str):
    if wind_deg is None or wind_kmh is None: return 0.5
    ideal_deg = cardinal_to_deg(vent_ideal_str.split("-")[0])
    if ideal_deg is None: return 0.5
    diff = angle_diff(wind_deg, ideal_deg)
    if diff <= 22.5: score_dir = 1.0
    elif diff <= 45: score_dir = 0.9
    elif diff <= 67.5: score_dir = 0.75
    elif diff <= 90: score_dir = 0.5
    elif diff <= 135: score_dir = 0.2
    else: score_dir = 0.0
    if wind_kmh < 15: score_force = 1.0
    elif wind_kmh < 25: score_force = 0.7
    elif wind_kmh < 35: score_force = 0.4
    else: score_force = 0.1
    return round(score_dir * 0.7 + score_force * 0.3, 2)


def qualify_vent(wind_deg, vent_ideal_str):
    """Qualifie le vent par rapport à l'idéal du spot."""
    if wind_deg is None:
        return "inconnu"
    ideal_deg = cardinal_to_deg(vent_ideal_str.split("-")[0])
    if ideal_deg is None:
        return "inconnu"
    diff = angle_diff(wind_deg, ideal_deg)
    if diff <= 45:
        return "offshore ✓"
    elif diff <= 90:
        return "cross-shore ~"
    else:
        return "onshore ✗"

def get_val(h, param):
    sources = h.get(param, {})
    val = sources.get("sg") or sources.get("noaa") or sources.get("meto")
    return round(val, 2) if val is not None else None

POIDS = {"direction_houle": 0.40, "periode": 0.25, "vent": 0.25, "taille_houle": 0.10}

def charger_spots():
    spots = []
    with open(DB_PATH, newline='', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            spots.append({
                "nom": row["nom"], "region": row["region"],
                "lat": float(row["lat"]), "lng": float(row["lng"]),
                "houle_dir": row["houle_direction"],
                "houle_min": float(row["houle_taille_min_m"]),
                "houle_max": float(row["houle_taille_max_m"]),
                "vent_ideal": row["vent_direction_ideal"],
                "maree_ideale": row["maree_ideale"],
                "type_break": row["type_break"],
                "type_vague": row["type_vague"],
                "crowd": row["crowd"],
            })
    return spots

def fetch_stormglass(lat, lng):
    params = "swellHeight,swellDirection,swellPeriod,windSpeed,windDirection"
    r = requests.get(
        "https://api.stormglass.io/v2/weather/point",
        params={"lat": lat, "lng": lng, "params": params},
        headers={"Authorization": STORMGLASS_API_KEY},
        timeout=10
    )
    if r.status_code != 200: return None
    return r.json()

def scorer_spot_heure(spot, h):
    swell_h   = get_val(h, "swellHeight")
    swell_dir = get_val(h, "swellDirection")
    swell_per = get_val(h, "swellPeriod")
    wind_ms   = get_val(h, "windSpeed")
    wind_dir  = get_val(h, "windDirection")
    wind_kmh  = round(wind_ms * 3.6, 1) if wind_ms else None

    if swell_h and swell_h > spot["houle_max"]:
        return None, f"houle trop grosse ({swell_h}m)"
    if wind_kmh and wind_dir and wind_kmh > 30:
        ideal_deg = cardinal_to_deg(spot["vent_ideal"].split("-")[0])
        if ideal_deg and angle_diff(wind_dir, ideal_deg) > 45:
            return None, f"vent fort mauvaise direction ({wind_kmh}km/h {deg_to_cardinal(wind_dir)})"

    s = (
        direction_in_range(swell_dir, spot["houle_dir"]) * POIDS["direction_houle"] +
        score_periode(swell_per) * POIDS["periode"] +
        score_vent(wind_kmh, wind_dir, spot["vent_ideal"]) * POIDS["vent"] +
        score_taille(swell_h, spot["houle_min"], spot["houle_max"]) * POIDS["taille_houle"]
    )
    return {
        "score": round(s * 10, 1),
        "houle": f"{swell_h}m" if swell_h else "—",
        "dir_houle": deg_to_cardinal(swell_dir),
        "dir_ideale": spot["houle_dir"],
        "periode": f"{swell_per}s" if swell_per else "—",
        "vent": f"{wind_kmh}km/h {deg_to_cardinal(wind_dir)}" if wind_kmh else "—",
        "vent_ideal": spot["vent_ideal"],
        "vent_qualite": qualify_vent(wind_dir, spot["vent_ideal"]),
        "maree": spot["maree_ideale"],
        "type_vague": spot["type_vague"],
        "crowd": spot["crowd"],
    }, None

# ─── Cache helper ────────────────────────────────────────────

def get_forecast_cache():
    """Retourne le cache forecast si disponible (injecté par 04_api.py)."""
    return _FORECAST_CACHE.get("data")

# Sera injecté par 04_api au démarrage
_FORECAST_CACHE = {}

# ─── Fonctions outils (appelées directement, sans LangGraph) ──

def _classement_from_cache(cached, region, creneau):
    """Construit un classement depuis le cache forecast."""
    resultats = []
    elimines = []

    for label, spots in cached.items():
        # Filtre sur le créneau demandé
        if creneau and label.split(" ")[1] != creneau:
            continue
        for s in spots:
            if region and region.lower() not in s["region"].lower():
                continue
            if s["elimine"]:
                elimines.append({"nom": s["nom"], "raison": s["raison"], "creneau": label})
            else:
                resultats.append({"nom": s["nom"], "region": s["region"],
                                  "creneau": label, **{k: s[k] for k in
                                  ["score","houle","dir_houle","dir_ideale","periode",
                                   "vent","vent_ideal","vent_qualite","maree","type_vague","crowd"]}})

    # Grouper par date, meilleur score par spot par jour
    from collections import defaultdict
    meilleurs = {}
    for r in resultats:
        date = r["creneau"].split(" ")[0]
        key = (r["nom"], date)
        if key not in meilleurs or r["score"] > meilleurs[key]["score"]:
            meilleurs[key] = r

    par_date = defaultdict(list)
    for r in sorted(meilleurs.values(), key=lambda x: (x["creneau"], -x["score"])):
        date = r["creneau"].split(" ")[0]
        par_date[date].append(r)

    output = []
    for date in sorted(par_date.keys()):
        spots_du_jour = sorted(par_date[date], key=lambda x: -x["score"])
        output.append(f"\n=== {date} ===")
        for i, r in enumerate(spots_du_jour, 1):
            output.append(f"{i}. {r['nom']} ({r['region']}) [{r['creneau']}] — Score: {r['score']}/10 | "
                f"Houle: {r['houle']} {r['dir_houle']} (idéal: {r['dir_ideale']}) | "
                f"Période: {r['periode']} | Vent: {r['vent']} (idéal: {r['vent_ideal']}, qualité: {r['vent_qualite']}) | "
                f"Marée: {r['maree']} | Vague: {r['type_vague']} | Crowd: {r['crowd']}")
    if elimines:
        output.append(f"\nÉliminés : {', '.join(set(e['nom'] for e in elimines))}")
    return "\n".join(output) if output else "Aucun résultat (depuis cache)."

def tool_get_classement(region: str = "", creneau: str = "") -> str:
    # Utiliser le cache si disponible
    cached = get_forecast_cache()
    if cached:
        return _classement_from_cache(cached, region, creneau)

    spots = charger_spots()
    if region:
        spots = [s for s in spots if region.lower() in s["region"].lower()]

    resultats = []
    elimines = []

    for spot in spots:
        data = fetch_stormglass(spot["lat"], spot["lng"])
        if not data: continue
        heures = data.get("hours", [])

        cible_utc = None
        if creneau:
            try:
                heure_num = int(creneau.replace("h", "")) - 2
                cible_utc = heure_num % 24
            except: pass

        for h in heures:
            try:
                dt = datetime.fromisoformat(h["time"].replace("Z", "+00:00"))
                if cible_utc is not None:
                    if dt.hour != cible_utc: continue
                elif dt.hour not in CRENEAUX_UTC: continue

                info, raison = scorer_spot_heure(spot, h)
                heure_fr = (dt.hour + 2) % 24
                label = f"{dt.strftime('%d/%m')} {heure_fr:02d}h"

                if info:
                    resultats.append({"nom": spot["nom"], "region": spot["region"],
                                      "creneau": label, **info})
                else:
                    elimines.append({"nom": spot["nom"], "raison": raison, "creneau": label})

                # Si créneau précis demandé, on s'arrête au premier match
                # Sinon on continue pour couvrir tous les créneaux des 7 jours
                if cible_utc is not None:
                    break
            except: continue

    # Garder uniquement le meilleur créneau par spot par jour
    from collections import defaultdict
    meilleurs = {}  # clé: (nom, date) → meilleur résultat
    for r in resultats:
        date = r["creneau"].split(" ")[0]  # ex: "08/06"
        key = (r["nom"], date)
        if key not in meilleurs or r["score"] > meilleurs[key]["score"]:
            meilleurs[key] = r

    resultats_filtres = sorted(meilleurs.values(), key=lambda x: (x["creneau"], -x["score"]))

    # Grouper par date pour affichage lisible
    from collections import defaultdict
    par_date = defaultdict(list)
    for r in resultats_filtres:
        date = r["creneau"].split(" ")[0]
        par_date[date].append(r)

    output = []
    for date in sorted(par_date.keys()):
        spots_du_jour = sorted(par_date[date], key=lambda x: -x["score"])
        output.append(f"\n=== {date} ===")
        for i, r in enumerate(spots_du_jour, 1):
            output.append(f"{i}. {r['nom']} ({r['region']}) [{r['creneau']}] — Score: {r['score']}/10 | "
                f"Houle: {r['houle']} {r['dir_houle']} (idéal: {r['dir_ideale']}) | "
                f"Période: {r['periode']} | Vent: {r['vent']} (idéal: {r['vent_ideal']}, qualité: {r['vent_qualite']}) | "
                f"Marée: {r['maree']} | Vague: {r['type_vague']} | Crowd: {r['crowd']}")

    if elimines:
        output.append(f"\nÉliminés : {', '.join(set(e['nom'] for e in elimines))}")
    return "\n".join(output) if output else "Aucun résultat."

def tool_get_spot(nom_spot: str, creneau: str = "") -> str:
    spots = charger_spots()
    match = next((s for s in spots if nom_spot.lower() in s["nom"].lower()), None)
    if not match: return f"Spot '{nom_spot}' non trouvé dans la DB."

    data = fetch_stormglass(match["lat"], match["lng"])
    if not data: return "Erreur fetch météo."

    heures = data.get("hours", [])
    cible_utc = None
    if creneau:
        try:
            cible_utc = (int(creneau.replace("h", "")) - 2) % 24
        except: pass

    header = (f"Spot : {match['nom']} ({match['region']})\n"
              f"\n--- DONNÉES DB (conditions idéales) ---\n"
              f"Houle idéale : {match['houle_dir']} | min {match['houle_min']}m | max {match['houle_max']}m\n"
              f"Vent idéal   : {match['vent_ideal']}\n"
              f"Marée idéale : {match['maree_ideale']}\n"
              f"Type break   : {match['type_break']} | Vague : {match['type_vague']} | Crowd : {match['crowd']}\n"
              f"\n--- CONDITIONS RÉELLES (forecast) ---\n")

    resultats = []
    for h in heures:
        try:
            dt = datetime.fromisoformat(h["time"].replace("Z", "+00:00"))
            if cible_utc is not None:
                if dt.hour != cible_utc: continue
            elif dt.hour not in CRENEAUX_UTC: continue

            info, raison = scorer_spot_heure(match, h)
            heure_fr = (dt.hour + 2) % 24
            label = f"{dt.strftime('%d/%m')} {heure_fr:02d}h"

            if info:
                resultats.append(f"{label} — Score: {info['score']}/10 | "
                    f"Houle: {info['houle']} {info['dir_houle']} | Période: {info['periode']} | Vent: {info['vent']}")
            else:
                resultats.append(f"{label} — ÉLIMINÉ : {raison}")
            if creneau: break
        except: continue

    return header + "\n".join(resultats) if resultats else header + "Aucune donnée disponible."

def tool_get_spots_near(lieu: str, rayon_km: float = 60) -> str:
    lat, lng = geocode(lieu)
    if lat is None: return f"Impossible de localiser '{lieu}'."

    spots = charger_spots()
    spots_proches = sorted(
        [(distance_km(lat, lng, s["lat"], s["lng"]), s) for s in spots],
        key=lambda x: x[0]
    )
    spots_proches = [(d, s) for d, s in spots_proches if d <= rayon_km]

    if not spots_proches:
        return f"Aucun spot dans la DB à moins de {rayon_km}km de {lieu}."

    output = [f"Spots à moins de {rayon_km}km de {lieu} ({lat:.3f}, {lng:.3f}):\n"]
    for dist, spot in spots_proches:
        data = fetch_stormglass(spot["lat"], spot["lng"])
        if not data: continue
        for h in data.get("hours", []):
            try:
                dt = datetime.fromisoformat(h["time"].replace("Z", "+00:00"))
                if dt.hour not in CRENEAUX_UTC: continue
                info, raison = scorer_spot_heure(spot, h)
                heure_fr = (dt.hour + 2) % 24
                label = f"{dt.strftime('%d/%m')} {heure_fr:02d}h"
                if info:
                    output.append(f"• {spot['nom']} ({dist:.0f}km) [{label}] — Score: {info['score']}/10 | "
                        f"Houle: {info['houle']} {info['dir_houle']} (idéal: {info['dir_ideale']}) | "
                        f"Période: {info['periode']} | Vent: {info['vent']} (idéal: {info['vent_ideal']}, qualité: {info['vent_qualite']}) | "
                        f"Vague: {spot['type_vague']} | Crowd: {spot['crowd']}")
                else:
                    output.append(f"• {spot['nom']} ({dist:.0f}km) — ÉLIMINÉ: {raison}")
                break
            except: continue
    return "\n".join(output)

# ─── Agent Mistral natif (sans LangGraph) ─────────────────────

TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "get_classement",
            "description": "Classement des spots scorés. Utilise creneau='' pour tous les créneaux et raisonner sur la meilleure fenêtre temporelle.",
            "parameters": {
                "type": "object",
                "properties": {
                    "region": {"type": "string", "description": "Filtre région ex: 'Pays Basque', 'Asturies', '' pour tout"},
                    "creneau": {"type": "string", "description": "Heure FR ex: '10h', '' pour tous les créneaux"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_spot",
            "description": "Conditions détaillées d'un spot précis avec données DB complètes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "nom_spot": {"type": "string", "description": "Nom du spot ex: 'Mundaka', 'Hossegor'"},
                    "creneau": {"type": "string", "description": "Heure FR ex: '10h', '' pour tous"}
                },
                "required": ["nom_spot"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_spots_near",
            "description": "Spots proches d'une ville avec conditions. Utiliser quand l'utilisateur mentionne une localisation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "lieu": {"type": "string", "description": "Ville ou lieu ex: 'Hendaye', 'San Sebastian', 'Biarritz'"},
                    "rayon_km": {"type": "number", "description": "Rayon recherche en km (défaut 60)"}
                },
                "required": ["lieu"]
            }
        }
    }
]

TOOL_FUNCTIONS = {
    "get_classement": tool_get_classement,
    "get_spot": tool_get_spot,
    "get_spots_near": tool_get_spots_near,
}

def get_system_prompt():
    from datetime import datetime, timedelta
    today = datetime.now()
    saturday = today + timedelta(days=(5 - today.weekday()) % 7)
    sunday = saturday + timedelta(days=1)
    return f"""Tu es SurfAgent, un assistant expert en surf sur la côte atlantique française et espagnole.
Tu as acces a une base de donnees de 25 spots et aux conditions meteo marines en temps reel.

Tes outils :
- get_classement(region, creneau) : classement des spots scores. creneau='' pour tous les creneaux.
- get_spot(nom_spot, creneau) : conditions detaillees d un spot avec toutes les donnees DB.
- get_spots_near(lieu, rayon_km) : spots proches d une ville avec leurs conditions.

Regles absolues :
- Ne JAMAIS donner de conditions, scores ou donnees meteo sans avoir appele un outil dans cette reponse.
- Si pas de donnees fraiches, dire "je n ai pas cette information" plutot qu extrapoler.
- Pour "je suis a X" ou "near X" ou "autour de X" utiliser get_spots_near.
- Pour un weekend ou une periode : get_classement avec creneau='' puis filtrer sur samedi {saturday.strftime('%d/%m')} et dimanche {sunday.strftime('%d/%m')} uniquement.
- Repondre dans la meme langue que l utilisateur (FR ou EN), termes techniques surf toujours en anglais.
- Recommandation claire avec reasoning : score, conditions cles, pourquoi ce spot plutot qu un autre.
- La qualification du vent (offshore/cross-shore/onshore) est DEJA calculee dans les donnees : utilise le champ vent_qualite, ne le recalcule JAMAIS toi-meme.
- Un vent offshore est ideal (vent de la terre vers la mer, vagues propres). Un vent onshore est mauvais.
- Pour le vent, TOUJOURS mentionner : magnitude en km/h + direction + qualite. Exemple : 5km/h NW (offshore). Jamais de qualite seule sans magnitude.
- Ne donne JAMAIS de conseils non bases sur les donnees : pas de temperature d eau, pas de conseils materiel.
- Aujourd hui : {today.strftime('%A %d/%m/%Y')}. Stormglass couvre 7 jours max. Si la date demandee depasse cette fenetre, dire clairement : Je n ai pas de donnees pour cette date (hors 7 jours). Je peux donner les conditions actuelles pour evaluer le spot, ou reviens plus proche de la date. Ne jamais utiliser les donnees actuelles comme proxy sans le signaler.
- Echelle absolue des periodes de houle :
  * moins de 8s : windsurf/chop, vagues sans forme. Mauvais.
  * 8-10s : court, vagues mediocres, peu d energie. Passable.
  * 10-12s : correct, houle organisee, sessions honorables.
  * 12-14s : bon, belle houle de fond, vagues de qualite.
  * plus de 14s : excellent, houle de fond pure, conditions de reve.
  Toujours qualifier la periode en absolu ET en relatif si c est le meilleur disponible. Ex : 8.3s (passable en absolu, meilleur disponible ce weekend).
"""

def invoke(user_message: str) -> str:
    """Appelle Mistral directement avec function calling natif."""
    messages = [
        {"role": "system", "content": get_system_prompt()},
        {"role": "user", "content": user_message}
    ]

    headers = {
        "Authorization": f"Bearer {MISTRAL_API_KEY}",
        "Content-Type": "application/json"
    }

    MAX_TURNS = 8
    for _ in range(MAX_TURNS):
        payload = {
            "model": "mistral-small-latest",
            "messages": messages,
            "tools": TOOLS_SCHEMA,
            "tool_choice": "auto",
            "temperature": 0.1,
        }

        r = requests.post(
            "https://api.mistral.ai/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=30
        )

        if r.status_code != 200:
            return f"Erreur API Mistral: {r.status_code} — {r.text}"

        data = r.json()
        choice = data["choices"][0]
        message = choice["message"]
        messages.append(message)

        # Si pas de tool call → réponse finale
        if not message.get("tool_calls"):
            return message.get("content", "Pas de réponse.")

        # Exécuter les tool calls
        for tc in message["tool_calls"]:
            fn_name = tc["function"]["name"]
            fn_args = json.loads(tc["function"]["arguments"])
            fn = TOOL_FUNCTIONS.get(fn_name)

            if fn:
                result = fn(**fn_args)
            else:
                result = f"Outil '{fn_name}' non trouvé."

            messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": result
            })

    return "Désolé, je n'ai pas pu traiter ta demande (trop d'itérations)."
