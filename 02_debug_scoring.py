from agent_core import (fetch_stormglass, scorer, charger_spots,
                         direction_score, periode_score, taille_score,
                         vent_categorie, vent_range,
                         get_val, POIDS, deg_to_cardinal)
from datetime import datetime, timedelta

# ── Config ──────────────────────────────────────────────
CIBLE_DATE     = (datetime.now() + timedelta(days=1)).strftime('%d/%m')
CIBLE_HEURE_FR = 7
CIBLE_HEURE_UTC = CIBLE_HEURE_FR - 2

# ── Fetch et score tous les spots ────────────────────────
spots     = charger_spots()
resultats = []
elimines  = []

for spot in spots:
    data = fetch_stormglass(spot["lat"], spot["lng"])
    if not data:
        print(f"  [ERR] {spot['nom']} — fetch échoué")
        continue

    for h in data["hours"]:
        try:
            dt = datetime.fromisoformat(h["time"].replace("Z", "+00:00"))
            if dt.strftime('%d/%m') != CIBLE_DATE or dt.hour != CIBLE_HEURE_UTC:
                continue

            # Données brutes
            swell_h   = get_val(h, "swellHeight")
            swell_dir = get_val(h, "swellDirection")
            swell_per = get_val(h, "swellPeriod")
            wind_ms   = get_val(h, "windSpeed")
            wind_dir  = get_val(h, "windDirection")
            wind_kmh  = round(wind_ms * 3.6, 1) if wind_ms else None

            # Sous-scores
            s_dir    = direction_score(swell_dir, spot["houle_dir"])
            s_per    = periode_score(swell_per)
            s_taille = taille_score(swell_h, spot["houle_min"], spot["houle_max"])

            # Vent : categorie + range
            cat = vent_categorie(wind_dir, spot["vent_ideal"])
            rng = vent_range(wind_kmh, cat)

            # Score brut renormalisé (sans vent)
            poids_total = POIDS["direction"] + POIDS["periode"] + POIDS["taille"]
            score_brut  = (s_dir * POIDS["direction"] +
                           s_per * POIDS["periode"] +
                           s_taille * POIDS["taille"]) / poids_total

            info, raison = scorer(spot, h)

            if raison:
                elimines.append({
                    "nom":    spot["nom"],
                    "region": spot["region"],
                    "raison": raison,
                    "houle":  f"{swell_h}m",
                    "periode": f"{swell_per}s",
                    "vent":   f"{wind_kmh}km/h",
                })
            else:
                rng_min, rng_max = rng
                resultats.append({
                    "nom":         spot["nom"],
                    "region":      spot["region"],
                    "score":       info["score"],
                    "houle_h":     swell_h,
                    "houle_dir":   swell_dir,
                    "houle_ideal": spot["houle_dir"],
                    "s_dir":       s_dir,
                    "periode":     swell_per,
                    "s_per":       s_per,
                    "wind_kmh":    wind_kmh,
                    "wind_dir":    wind_dir,
                    "vent_ideal":  spot["vent_ideal"],
                    "vent_cat":    cat,
                    "rng_min":     rng_min,
                    "rng_max":     rng_max,
                    "score_brut":  round(score_brut, 2),
                    "taille_min":  spot["houle_min"],
                    "taille_max":  spot["houle_max"],
                    "s_taille":    s_taille,
                })
            break
        except:
            continue

# ── Affichage ─────────────────────────────────────────────
resultats.sort(key=lambda x: -x["score"])

print(f"\n{'='*130}")
print(f"SCORING DÉTAILLÉ — {CIBLE_DATE} {CIBLE_HEURE_FR:02d}h")
print(f"Logique : score_brut(Dir {POIDS['direction']*100:.0f}% + Pér {POIDS['periode']*100:.0f}% + Taille {POIDS['taille']*100:.0f}%) mappé dans range défini par le vent")
print(f"{'='*130}")
print(f"{'#':<3} {'Spot':<30} {'Score':>6} | {'Houle':>8} {'Dir→Idéal':<14} {'S.Dir':>6} | "
      f"{'Pér':>6} {'S.Pér':>6} | {'Vent':>12} {'Cat':<10} {'Range':<10} {'Brut':>6} | {'Taille':>8} {'S.Tail':>7}")
print(f"{'-'*130}")

for i, r in enumerate(resultats, 1):
    houle_str  = f"{r['houle_h']}m {deg_to_cardinal(r['houle_dir'])}" if r['houle_h'] else "—"
    vent_str   = f"{r['wind_kmh']}km/h {deg_to_cardinal(r['wind_dir'])}" if r['wind_kmh'] else "—"
    taille_str = f"{r['taille_min']}-{r['taille_max']}m"
    dir_ideale = f"{deg_to_cardinal(r['houle_dir'])}→{r['houle_ideal']}"
    range_str  = f"({r['rng_min']}-{r['rng_max']})"

    print(f"{i:<3} {r['nom']:<30} {r['score']:>6} | "
          f"{houle_str:>8} {dir_ideale:<14} {r['s_dir']:>6.2f} | "
          f"{r['periode']:>6.1f}s {r['s_per']:>6.2f} | "
          f"{vent_str:>12} {r['vent_cat']:<10} {range_str:<10} {r['score_brut']:>6.2f} | "
          f"{taille_str:>8} {r['s_taille']:>7.2f}")

if elimines:
    print(f"\n── Éliminés ({len(elimines)}) ──")
    for e in elimines:
        print(f"  ✗ {e['nom']} ({e['region']}) — {e['raison']} | Houle: {e['houle']} | Période: {e['periode']} | Vent: {e['vent']}")
