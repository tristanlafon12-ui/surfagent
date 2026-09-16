from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
import uvicorn

from agent_core import invoke, tool_get_classement, charger_spots, _FORECAST_CACHE
import json

app = FastAPI()

# Cache en mémoire — expire après 1h
_forecast_cache = {"data": None, "timestamp": None}
CACHE_TTL_SECONDS = 3600

def get_cached_forecast():
    """Retourne le cache si valide, None sinon."""
    import time
    if _forecast_cache["data"] and _forecast_cache["timestamp"]:
        age = time.time() - _forecast_cache["timestamp"]
        if age < CACHE_TTL_SECONDS:
            return _forecast_cache["data"]
    return None

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="."), name="static")

class ChatRequest(BaseModel):
    message: str

class ChatResponse(BaseModel):
    response: str

@app.get("/")
async def root():
    return FileResponse("index.html")

@app.get("/forecast")
async def forecast():
    """Retourne tous les scores par spot par créneau sur 10 jours — fetches en parallèle."""
    import asyncio
    from concurrent.futures import ThreadPoolExecutor
    from agent_core import fetch_stormglass, scorer_spot_heure
    from datetime import datetime
    import time

    # Retourner le cache si valide
    if _forecast_cache["data"] and _forecast_cache["timestamp"]:
        age = time.time() - _forecast_cache["timestamp"]
        if age < CACHE_TTL_SECONDS:
            return _forecast_cache["data"]

    spots = charger_spots()
    CRENEAUX_UTC = [5, 8, 11, 14, 17, 19]
    output = {}

    def process_spot(spot):
        data = fetch_stormglass(spot["lat"], spot["lng"])
        if not data:
            return []
        results = []
        for h in data.get("hours", []):
            try:
                dt = datetime.fromisoformat(h["time"].replace("Z", "+00:00"))
                if dt.hour not in CRENEAUX_UTC:
                    continue
                heure_fr = (dt.hour + 2) % 24
                label = f"{dt.strftime('%d/%m')} {heure_fr:02d}h"
                info, raison = scorer_spot_heure(spot, h)
                results.append((label, {
                    "nom": spot["nom"],
                    "region": spot["region"],
                    "lat": spot["lat"],
                    "lng": spot["lng"],
                    "score": info["score"] if info else 0,
                    "elimine": raison is not None,
                    "raison": raison or "",
                    "houle": info["houle"] if info else "—",
                    "dir_houle": info["dir_houle"] if info else "—",
                    "dir_ideale": info["dir_ideale"] if info else "—",
                    "periode": info["periode"] if info else "—",
                    "vent": info["vent"] if info else "—",
                    "vent_ideal": info["vent_ideal"] if info else "—",
                    "vent_qualite": info["vent_qualite"] if info else "—",
                    "maree": info["maree"] if info else "—",
                    "type_vague": info["type_vague"] if info else "—",
                    "crowd": info["crowd"] if info else "—",
                }))
            except:
                continue
        return results

    # Fetches en parallèle — 25 spots simultanément
    loop = asyncio.get_event_loop()
    with ThreadPoolExecutor(max_workers=25) as executor:
        all_results = await loop.run_in_executor(
            None,
            lambda: list(executor.map(process_spot, spots))
        )

    for spot_results in all_results:
        for label, entry in spot_results:
            if label not in output:
                output[label] = []
            output[label].append(entry)

    # Sauvegarder en cache local + cache partagé avec l'agent
    _forecast_cache["data"] = output
    _forecast_cache["timestamp"] = time.time()
    _FORECAST_CACHE["data"] = output  # partagé avec agent_core

    return output

@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    response = invoke(request.message)
    return ChatResponse(response=response)

if __name__ == "__main__":
    uvicorn.run("04_api:app", host="0.0.0.0", port=8000, reload=False)
