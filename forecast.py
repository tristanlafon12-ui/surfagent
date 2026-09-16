"""
Source unique de vérité pour les données Stormglass.
Cache en mémoire TTL 1h. Utilisé par agent_core et 04_api.
"""
import time
from concurrent.futures import ThreadPoolExecutor

_cache = {"data": None, "ts": 0}
TTL = 3600  # 1h

def _is_valid():
    return _cache["data"] is not None and (time.time() - _cache["ts"]) < TTL

def get_cache():
    """Retourne le cache si valide, None sinon."""
    return _cache["data"] if _is_valid() else None

def set_cache(data):
    _cache["data"] = data
    _cache["ts"] = time.time()

def get_or_fetch_all():
    """
    Retourne {nom_spot: stormglass_data} pour tous les spots.
    Utilise le cache si valide, sinon fetch en parallèle.
    """
    if _is_valid():
        return _cache["data"]
    return fetch_all_and_cache()

def get_or_fetch_spot(spot):
    """
    Retourne les données Stormglass pour un spot.
    Utilise le cache si valide.
    """
    cached = get_cache()
    if cached:
        return cached.get(spot["nom"])
    # Pas de cache — fetch individuel
    from agent_core import fetch_stormglass
    return fetch_stormglass(spot["lat"], spot["lng"])

def fetch_all_and_cache():
    """Fetch tous les spots en parallèle, met en cache, retourne le dict."""
    from agent_core import charger_spots, fetch_stormglass
    spots = charger_spots()

    def fetch_one(spot):
        data = fetch_stormglass(spot["lat"], spot["lng"])
        return spot["nom"], data

    with ThreadPoolExecutor(max_workers=25) as ex:
        results = list(ex.map(fetch_one, spots))

    data = {nom: d for nom, d in results if d is not None}
    set_cache(data)
    return data

def build_forecast_json():
    """
    Construit le JSON pour la carte : {label: [spot_scoré, ...]}
    Utilisé par l'endpoint /forecast de FastAPI.
    """
    from agent_core import charger_spots, scorer, deg_to_cardinal, CRENEAUX_UTC
    from datetime import datetime

    all_data = get_or_fetch_all()
    spots = charger_spots()
    output = {}

    for spot in spots:
        data = all_data.get(spot["nom"])
        if not data: continue
        for h in data.get("hours", []):
            try:
                dt = datetime.fromisoformat(h["time"].replace("Z", "+00:00"))
                if dt.hour not in CRENEAUX_UTC: continue
                heure_fr = (dt.hour + 2) % 24
                label = f"{dt.strftime('%d/%m')} {heure_fr:02d}h"
                info, raison = scorer(spot, h)
                if label not in output: output[label] = []
                output[label].append({
                    "nom":        spot["nom"],
                    "region":     spot["region"],
                    "lat":        spot["lat"],
                    "lng":        spot["lng"],
                    "elimine":    info is None,
                    "raison":     raison or "",
                    "score":      info["score"]       if info else 0,
                    "houle":      info["houle"]       if info else "—",
                    "dir_houle":  info["dir_houle"]   if info else "—",
                    "dir_ideale": info["dir_ideale"]  if info else "—",
                    "periode":    info["periode"]     if info else "—",
                    "vent":       info["vent"]        if info else "—",
                    "vent_ideal": info["vent_ideal"]  if info else "—",
                    "vent_qualite": info["vent_qualite"] if info else "—",
                    "maree":      info["maree"]       if info else "—",
                    "type_vague": info["type_vague"]  if info else "—",
                    "crowd":      info["crowd"]       if info else "—",
                })
            except: continue
    return output
