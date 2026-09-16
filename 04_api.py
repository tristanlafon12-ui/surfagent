from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
import uvicorn

from agent_core import invoke
from forecast import build_forecast_json, fetch_all_and_cache

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.mount("/static", StaticFiles(directory="."), name="static")

class ChatRequest(BaseModel):
    message: str

class ChatResponse(BaseModel):
    response: str

@app.get("/")
async def root():
    return FileResponse("index.html")

@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    return ChatResponse(response=invoke(req.message))

@app.get("/forecast")
async def forecast():
    """Retourne le JSON de tous les spots scorés sur 10 jours."""
    import asyncio
    loop = asyncio.get_event_loop()
    data = await loop.run_in_executor(None, build_forecast_json)
    return data

@app.post("/forecast/refresh")
async def forecast_refresh():
    """Force un nouveau fetch Stormglass (ignore le cache)."""
    import asyncio
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, fetch_all_and_cache)
    data = await loop.run_in_executor(None, build_forecast_json)
    return data

if __name__ == "__main__":
    uvicorn.run("04_api:app", host="0.0.0.0", port=8000, reload=False)
