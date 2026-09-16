"""
05_newsletter.py
Genere et envoie le surf report quotidien via SendGrid.
Usage : python 05_newsletter.py
"""
import os
import json
import requests
from datetime import datetime, timedelta
from dotenv import load_dotenv
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

load_dotenv()
MISTRAL_API_KEY  = os.getenv("MISTRAL_API_KEY")
SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY")
FROM_EMAIL       = os.getenv("NEWSLETTER_FROM")
TO_EMAILS        = [e.strip() for e in os.getenv("NEWSLETTER_TO", "").split(",") if e.strip()]

# ─── Données ──────────────────────────────────────────────────

def get_forecast_data():
    """
    Recupere les donnees depuis le cache si le serveur tourne,
    sinon fetch directement depuis Stormglass.
    """
    try:
        r = requests.get("http://localhost:8000/forecast", timeout=5)
        if r.status_code == 200:
            return r.json()
    except:
        pass

    # Serveur pas lance — fetch direct
    print("Serveur non disponible, fetch direct Stormglass...")
    from forecast import build_forecast_json
    return build_forecast_json()

def periode_label(p_str):
    """Qualifie la periode en absolu."""
    try:
        p = float(p_str.replace("s",""))
        if p < 8:   return "mauvais"
        elif p < 10: return "passable"
        elif p < 12: return "correct"
        elif p < 14: return "bon"
        else:        return "excellent"
    except: return ""

def format_spot(s, label):
    p_qual = periode_label(s["periode"])
    return (f"{s['nom']} ({s['region']}) [{label}] score:{s['score']}/10 | "
            f"houle:{s['houle']} {s['dir_houle']} (ideal:{s['dir_ideale']}) | "
            f"periode:{s['periode']} ({p_qual}) | "
            f"vent:{s['vent']} ({s['vent_qualite']}, ideal:{s['vent_ideal']}) | "
            f"crowd:{s['crowd']}")

def build_context(data):
    """Construit le contexte complet pour Mistral."""
    today    = datetime.now()
    saturday = today + timedelta(days=(5 - today.weekday()) % 7)
    sunday   = saturday + timedelta(days=1)

    today_str = today.strftime("%d/%m")
    sat_str   = saturday.strftime("%d/%m")
    sun_str   = sunday.strftime("%d/%m")

    today_spots, weekend_spots, all_scored = [], [], []
    region_scores = {}
    region_spots  = {}  # region → [(score, label, spot)]

    for label, spots in data.items():
        date = label.split(" ")[0]
        top = sorted([s for s in spots if not s["elimine"]], key=lambda x: -x["score"])[:4]
        for s in top:
            entry = format_spot(s, label)
            all_scored.append((s["score"], label, entry))
            region_scores.setdefault(s["region"], []).append(s["score"])
            region_spots.setdefault(s["region"], []).append((s["score"], label, entry))
            if date == today_str:
                today_spots.append(entry)
            if date in (sat_str, sun_str):
                weekend_spots.append(entry)

    # Meilleure region
    best_region = max(region_scores, key=lambda r: sum(region_scores[r])/len(region_scores[r])) if region_scores else "?"
    best_region_avg = round(sum(region_scores.get(best_region,[])) / max(len(region_scores.get(best_region,[])),1), 1)

    # Top 10 spots de la meilleure region avec detail
    best_region_detail = sorted(region_spots.get(best_region, []), key=lambda x: -x[0])[:10]

    # Meilleure session absolue sur 10 jours
    best_session = sorted(all_scored, key=lambda x: -x[0])[:1]

    return {
        "date":               today.strftime("%A %d/%m/%Y"),
        "today_str":          today_str,
        "sat_str":            sat_str,
        "sun_str":            sun_str,
        "today_spots":        "\n".join(today_spots) or "Pas de donnees pour aujourd'hui.",
        "weekend_spots":      "\n".join(weekend_spots) or "Pas de donnees pour ce weekend.",
        "top_global":         "\n".join(e for _, _, e in sorted(all_scored, key=lambda x: -x[0])[:5]),
        "best_region":        best_region,
        "best_region_avg":    best_region_avg,
        "best_region_detail": "\n".join(e for _, _, e in best_region_detail),
        "best_session":       "\n".join(e for _, _, e in best_session),
    }

# ─── Generation Mistral ───────────────────────────────────────

NEWSLETTER_PROMPT = """Tu es SurfAgent, et tu envoies le surf report quotidien a Tristan.

PROFIL TRISTAN
Surfeur intermediaire+, beach breaks et point breaks tranquilles.
Session parfaite : offshore leger, periode superieure a 12s, spot peu crowd. La taille c'est du bonus. Une petite houle propre avec offshore et periode longue bat toujours une grosse houle desorganisee avec vent onshore.
Une mauvaise journee c'est : vent fort onshore ET periode courte (moins de 8s). Pas une houle petite.
Ne jamais dire "trop petit" si le vent est offshore et la periode correcte. Juger sur la qualite globale.
Houle superieure a 2m sur reef ou point break serieux = signaler que c'est technique pour son niveau.
Il peut se deplacer, a plusieurs boards, pas de contrainte de calendrier.

STYLE
Tu parles comme Chicken Joe de Surf's Up : decontracte, naturel, un pote qui sait de quoi il parle.
Zero emoji. Pas de ponctuation excessive. Pas de "Hey !" ou "Salut !".
Si les conditions sont nulles, tu le dis clairement. Si c'est exceptionnel quelque part, tu le dis avec le meme enthousiasme que Chicken Joe devant une vague parfaite.
Quand la meilleure session et la meilleure region convergent, propose un narratif de voyage — comme si tu planifiais le trip avec lui.

FORMAT — respecte ces titres exactement, dans cet ordre :

Surf Report — {date}

Aujourd'hui

[Top 2-3 spots du jour. Pour chaque spot : conditions actuelles VS conditions ideales du spot.
Format : "Houle NW 1.1m (ideal : N-W) | periode 9s (correct) | vent 5kmh SSE (offshore, ideal : S)".
Si c'est une journee a oublier, dis-le directement en une phrase et passe a la suite.]

Ce weekend — sam {sat_str} et dim {sun_str}

[Top 3 spots par jour. Meme format conditions vs ideales. Meilleur creneau precise pour chacun.]

Region du moment — {best_region}

[Developpe : quels spots precisement, sur quels jours, avec quelles conditions.
Si 3 spots sont on fire sur 3 jours specifiques, je dois le savoir.
Conditions VS ideales pour chaque spot mentionne.
Contexte meteo : pourquoi cette region marche en ce moment.]

Meilleure session des 10 prochains jours

[Le spot, la date, le creneau, le score, le detail complet des conditions VS ideales.
Puis le narratif : si ca converge avec la meilleure region, propose le trip. Sois Chicken Joe.]

---

Donnees aujourd'hui :
{today_spots}

Donnees weekend :
{weekend_spots}

Top global 10 jours :
{top_global}

Region du moment : {best_region} (score moyen {best_region_avg}/10)

Detail region du moment :
{best_region_detail}

Meilleure session absolue :
{best_session}

Genere l'email maintenant. Commence directement par "Surf Report —". Pas d'introduction.
"""

def generate_email(context):
    prompt = NEWSLETTER_PROMPT.format(**context)
    r = requests.post(
        "https://api.mistral.ai/v1/chat/completions",
        headers={"Authorization": f"Bearer {MISTRAL_API_KEY}", "Content-Type": "application/json"},
        json={
            "model": "mistral-small-latest",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.4,
            "max_tokens": 1000,
        },
        timeout=30
    )
    if r.status_code != 200:
        raise Exception(f"Erreur Mistral {r.status_code}: {r.text}")
    return r.json()["choices"][0]["message"]["content"]

# ─── Envoi SendGrid ───────────────────────────────────────────

def send_email(subject, body):
    sg = SendGridAPIClient(SENDGRID_API_KEY)
    for to in TO_EMAILS:
        msg = Mail(
            from_email=FROM_EMAIL,
            to_emails=to,
            subject=subject,
            plain_text_content=body
        )
        sg.send(msg)
        print(f"Email envoye a {to}")

# ─── Main ─────────────────────────────────────────────────────

if __name__ == "__main__":
    today = datetime.now().strftime("%A %d/%m/%Y")
    print(f"Surf Report — {today}")
    print("Recuperation des donnees...")

    data = get_forecast_data()
    if not data:
        print("Erreur : impossible de recuperer les donnees Stormglass.")
        exit(1)

    print("Generation de l'email...")
    context = build_context(data)
    body = generate_email(context)

    print("\n" + "="*60)
    print(body)
    print("="*60 + "\n")

    if not TO_EMAILS:
        print("Aucun destinataire configure dans .env (NEWSLETTER_TO).")
        exit(0)

    confirm = input(f"Envoyer a {TO_EMAILS} ? (o/n) : ").strip().lower()
    if confirm == "o":
        subject = f"Surf Report — {today}"
        send_email(subject, body)
        print("Envoye.")
    else:
        print("Annule.")
