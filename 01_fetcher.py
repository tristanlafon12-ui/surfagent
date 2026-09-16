import requests
import json
from datetime import datetime, timezone
from dotenv import load_dotenv
import os

load_dotenv()
API_KEY = os.getenv("STORMGLASS_API_KEY")

# --- Spot de test : Hossegor La Gravière ---
SPOT = {
    "nom": "Hossegor - La Gravière",
    "lat": 43.6625,
    "lng": -1.4423
}

# Créneaux horaires à afficher (heure locale FR = UTC+2 en été)
CRENEAUX_UTC = [5, 8, 11, 14, 17, 19]  # = 7h, 10h, 13h, 16h, 19h, 21h heure française

PARAMS = ",".join([
    "waveHeight",
    "waveDirection",
    "wavePeriod",
    "windSpeed",
    "windDirection",
    "swellHeight",
    "swellDirection",
    "swellPeriod",
])

def deg_to_cardinal(deg):
    """Convertit des degrés en point cardinal (16 directions)."""
    if deg is None:
        return "—"
    directions = [
        "N", "NNE", "NE", "ENE",
        "E", "ESE", "SE", "SSE",
        "S", "SSO", "SO", "OSO",
        "O", "ONO", "NO", "NNO"
    ]
    index = round(deg / 22.5) % 16
    return directions[index]

def fetch_forecast(lat, lng):
    url = "https://api.stormglass.io/v2/weather/point"
    response = requests.get(
        url,
        params={"lat": lat, "lng": lng, "params": PARAMS},
        headers={"Authorization": API_KEY}
    )
    if response.status_code != 200:
        print(f"Erreur API : {response.status_code}")
        print(response.text)
        return None
    return response.json()

def get_val(heure, param):
    sources = heure.get(param, {})
    val = sources.get("sg") or sources.get("noaa") or sources.get("meto")
    return round(val, 2) if val is not None else None

def score_periode(periode):
    """Transforme la période en score de qualité 0-1."""
    if periode is None:
        return "—"
    if periode < 8:
        return 0.2
    elif periode < 10:
        return 0.5
    elif periode < 12:
        return 0.7
    elif periode < 14:
        return 0.9
    else:
        return 1.0

def afficher_forecast(data, spot_nom):
    heures = data.get("hours", [])

    # Filtrer sur les créneaux UTC voulus
    heures_filtrees = []
    for h in heures:
        try:
            dt = datetime.fromisoformat(h["time"].replace("Z", "+00:00"))
            if dt.hour in CRENEAUX_UTC:
                heures_filtrees.append((dt, h))
        except:
            continue

    print(f"\n{'='*75}")
    print(f"FORECAST — {spot_nom}")
    print(f"{'='*75}")
    print(f"{'Heure (FR)':<14} {'Houle(m)':<10} {'DirHoule':<10} {'Période(s)':<12} {'Score P':<10} {'Vent(km/h)':<12} {'DirVent'}")
    print(f"{'-'*85}")

    for dt, h in heures_filtrees:
        heure_fr = dt.hour + 2  # UTC+2 en été
        if heure_fr >= 24:
            heure_fr -= 24
        time_display = f"{dt.strftime('%d/%m')} {heure_fr:02d}h"

        swell_h       = get_val(h, "swellHeight")
        swell_dir_deg = get_val(h, "swellDirection")
        swell_per     = get_val(h, "swellPeriod")
        wind_spd      = get_val(h, "windSpeed")
        wind_dir_deg  = get_val(h, "windDirection")

        # Conversion vent m/s → km/h
        if isinstance(wind_spd, float):
            wind_spd = round(wind_spd * 3.6, 1)

        # Conversion degrés → points cardinaux
        swell_dir = deg_to_cardinal(swell_dir_deg)
        wind_dir  = deg_to_cardinal(wind_dir_deg)

        score_p = score_periode(swell_per)

        print(f"{time_display:<14} {str(swell_h):<10} {swell_dir:<10} {str(swell_per):<12} {str(score_p):<10} {str(wind_spd):<12} {wind_dir}")

    used = data.get('meta', {}).get('requestCount', '?')
    remaining = 10 - used if isinstance(used, int) else '?'
    print(f"\nRequêtes utilisées aujourd'hui : {used} / 10  (reste : {remaining})")

if __name__ == "__main__":
    print(f"Fetching forecast pour : {SPOT['nom']}...")
    data = fetch_forecast(SPOT["lat"], SPOT["lng"])

    if data:
        afficher_forecast(data, SPOT["nom"])
        with open("forecast_raw.json", "w") as f:
            json.dump(data, f, indent=2)
        print("Données brutes sauvegardées dans forecast_raw.json")
