# app/main.py
from dotenv import load_dotenv
load_dotenv()

import os
import time
import json
from typing import List, Optional, Dict
from datetime import datetime, timedelta
import requests
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles 
from dateutil import parser as dateparser
import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from pathlib import Path

app = FastAPI(title="LoL Matches API")

# Configuration des chemins
BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"

# Créer le dossier s'il n'existe pas
STATIC_DIR.mkdir(exist_ok=True)

print(f"📁 Dossier static: {STATIC_DIR}")
print(f"📁 Existe: {STATIC_DIR.exists()}")

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

@app.get("/widget")
def get_widget():
    """Retourne la page HTML du widget."""
    return FileResponse(str(STATIC_DIR / "lol-widget.html"))

# Configuration
PANDASCORE_API_KEY = os.getenv("PANDASCORE_API_KEY")
BASE_URL = "https://api.pandascore.co/lol"
LOCAL_TZ = pytz.timezone(os.getenv("LOCAL_TZ", "Europe/Zurich"))
MATCHES_CACHE_FILE = "matches_cache.json"

# Cache persistant
_tracked_matches = []
_last_refresh = None

# Scheduler
scheduler = BackgroundScheduler()


def _load_cache():
    """Charge le cache depuis le fichier JSON."""
    global _tracked_matches, _last_refresh
    try:
        if os.path.exists(MATCHES_CACHE_FILE):
            with open(MATCHES_CACHE_FILE, "r") as f:
                data = json.load(f)
                _tracked_matches = data.get("matches", [])
                _last_refresh = data.get("last_refresh")
                print(f"✅ Cache chargé : {len(_tracked_matches)} matchs trackés")
    except Exception as e:
        print(f"⚠️  Erreur chargement cache : {e}")
        _tracked_matches = []
        _last_refresh = None


def _save_cache():
    """Sauvegarde le cache dans le fichier JSON."""
    try:
        with open(MATCHES_CACHE_FILE, "w") as f:
            json.dump({
                "matches": _tracked_matches,
                "last_refresh": _last_refresh
            }, f, indent=2)
        print(f"💾 Cache sauvegardé : {len(_tracked_matches)} matchs")
    except Exception as e:
        print(f"⚠️  Erreur sauvegarde cache : {e}")


def _fetch_upcoming_matches(limit: int = 5) -> List[dict]:
    """Récupère les prochains matchs depuis PandaScore."""
    if not PANDASCORE_API_KEY:
        print("⚠️  PANDASCORE_API_KEY non défini")
        return []

    headers = {"Authorization": f"Bearer {PANDASCORE_API_KEY}"}
    params = {"per_page": limit}

    try:
        resp = requests.get(
            f"{BASE_URL}/matches/upcoming",
            headers=headers,
            params=params,
            timeout=10
        )
        if resp.status_code == 200:
            matches = resp.json()
            print(f"✅ Récupéré {len(matches)} matchs à venir")
            return matches
        else:
            print(f"❌ Erreur API : {resp.status_code}")
            return []
    except requests.RequestException as e:
        print(f"❌ Erreur réseau : {e}")
        return []


def _fetch_running_matches() -> List[dict]:
    """Récupère TOUS les matchs en cours depuis PandaScore."""
    if not PANDASCORE_API_KEY:
        print("⚠️  PANDASCORE_API_KEY non défini")
        return []

    headers = {"Authorization": f"Bearer {PANDASCORE_API_KEY}"}

    try:
        resp = requests.get(
            f"{BASE_URL}/matches/running",
            headers=headers,
            timeout=10
        )
        if resp.status_code == 200:
            matches = resp.json()
            print(f"✅ Récupéré {len(matches)} matchs en cours")
            return matches
        else:
            print(f"❌ Erreur API running: {resp.status_code}")
            return []
    except requests.RequestException as e:
        print(f"❌ Erreur réseau running: {e}")
        return []


def _fetch_match_by_id(match_id: int) -> dict:
    """Récupère un match spécifique par son ID (via filter)."""
    if not PANDASCORE_API_KEY:
        return None
    
    headers = {"Authorization": f"Bearer {PANDASCORE_API_KEY}"}
    
    try:
        # ✅ Utiliser filter[id] au lieu de /matches/{id}
        resp = requests.get(
            f"{BASE_URL}/matches",
            headers=headers,
            params={"filter[id]": match_id},
            timeout=5
        )
        
        if resp.status_code == 200:
            matches = resp.json()
            # filter[id] retourne une liste, on prend le premier élément
            if matches and len(matches) > 0:
                return matches[0]
            else:
                print(f"      ⚠️  Match {match_id} non trouvé")
                return None
        else:
            print(f"      ❌ Erreur {resp.status_code}")
            return None
    except Exception as e:
        print(f"      ❌ Erreur: {e}")
        return None


def _normalize(match: dict) -> dict:
    """Normalize un match pour l'affichage."""
    # Tournament name
    tournament_parts = []
    if match.get("league") and isinstance(match["league"], dict):
        league_name = match["league"].get("name")
        if league_name:
            tournament_parts.append(league_name)
    
    if match.get("serie") and isinstance(match["serie"], dict):
        serie_name = match["serie"].get("full_name") or match["serie"].get("season")
        if serie_name:
            tournament_parts.append(serie_name)
    
    if not tournament_parts and match.get("tournament") and isinstance(match["tournament"], dict):
        tournament_name = match["tournament"].get("name")
        if tournament_name:
            tournament_parts.append(tournament_name)
    
    tournament = " - ".join(tournament_parts) if tournament_parts else None

    # Scores
    scores_map = {}
    if match.get("results"):
        for result in match["results"]:
            team_id = result.get("team_id")
            score = result.get("score")
            if team_id is not None and score is not None:
                scores_map[team_id] = score

    # Teams
    teams = []
    for opp in match.get("opponents", []):
        opp_obj = opp.get("opponent") if isinstance(opp, dict) and opp.get("opponent") else opp
        team_id = opp_obj.get("id")
        score = scores_map.get(team_id) if scores_map else None
        
        teams.append({
            "id": team_id,
            "name": opp_obj.get("name"),
            "logo": opp_obj.get("image_url") or opp_obj.get("logo"),
            "score": score
        })

    # Times
    begin_at = match.get("begin_at")
    begin_utc_iso = begin_at
    begin_local = None
    begin_local_human = None
    if begin_at:
        try:
            dt = dateparser.parse(begin_at)
            dt_local = dt.astimezone(LOCAL_TZ)
            begin_local = dt_local.isoformat()
            begin_local_human = dt_local.strftime("%d/%m %H:%M")
        except Exception:
            pass

    # Status
    status = match.get("status")
    status_label = {
        "not_started": "À venir",
        "running": "En cours",
        "finished": "Terminé",
        "canceled": "Annulé",
        "postponed": "Reporté"
    }.get(status, status)

    return {
        "id": match.get("id"),
        "tournament": tournament,
        "teams": teams,
        "begin_at_utc": begin_utc_iso,
        "begin_at_local": begin_local,
        "begin_at_local_human": begin_local_human,
        "status": status,
        "status_label": status_label,
        "best_of": match.get("number_of_games") or match.get("match_type"),
        "last_update": datetime.now(LOCAL_TZ).isoformat()
    }


def update_scores():
    """Tâche planifiée : Mettre à jour les scores (matchs en cours + fetch direct si besoin)."""
    import time
    start_time = time.time()
    
    global _tracked_matches
    
    if not _tracked_matches:
        print("ℹ️  Aucun match tracké, rien à mettre à jour")
        return
    
    print(f"🔄 Mise à jour des scores pour {len(_tracked_matches)} matchs...")
    
    # ✅ Récupérer SEULEMENT les matchs en cours
    running_matches = _fetch_running_matches()
    
    if not running_matches:
        print("ℹ️  Aucun match en cours")
        # Vérifier quand même si des matchs running ont fini
        has_running = any(m.get("status") == "running" for m in _tracked_matches)
        if has_running:
            print("   🔍 Vérification des matchs qui étaient en cours...")
    
    running_by_id = {m.get("id"): m for m in running_matches}
    
    updated_count = 0
    for i, match in enumerate(_tracked_matches):
        match_id = match.get("id")
        current_status = match.get("status")
        
        # ✅ Si le match est dans /running, on le met à jour
        if match_id in running_by_id:
            fresh_data = running_by_id[match_id]
            old_match = _tracked_matches[i]
            _tracked_matches[i] = _normalize(fresh_data)
            updated_count += 1
            
            old_scores = [t.get("score") for t in old_match.get("teams", [])]
            new_scores = [t.get("score") for t in _tracked_matches[i].get("teams", [])]
            new_status = _tracked_matches[i].get("status")
            
            if current_status != new_status:
                print(f"  ✓ Match {match_id} : {current_status} → {new_status} ({old_scores} → {new_scores})")
            else:
                print(f"  ✓ Match {match_id} mis à jour : {old_scores} → {new_scores}")
        
        # ✅ Si le match était running mais n'est plus dans /running, fetch direct par ID
        elif current_status == "running":
            print(f"  🔍 Match {match_id} absent de /running, récupération directe...")
            direct_match = _fetch_match_by_id(match_id)
            if direct_match:
                old_match = _tracked_matches[i]
                _tracked_matches[i] = _normalize(direct_match)
                
                old_scores = [t.get("score") for t in old_match.get("teams", [])]
                new_scores = [t.get("score") for t in _tracked_matches[i].get("teams", [])]
                new_status = _tracked_matches[i].get("status")
                
                print(f"  ✅ Match {match_id} récupéré : {current_status} → {new_status} ({old_scores} → {new_scores})")
                updated_count += 1
            else:
                print(f"  ⚠️  Impossible de récupérer le match {match_id}")
    
    _last_refresh = datetime.now(LOCAL_TZ).isoformat()
    _save_cache()
    
    elapsed = time.time() - start_time
    print(f"⏱️  update_scores terminé en {elapsed:.2f}s")
    
    if updated_count > 0:
        print(f"✅ {updated_count} matchs mis à jour")
    else:
        print("ℹ️  Aucun match à mettre à jour")


def refresh_matches_list():
    """Tâche planifiée : Rafraîchir la liste des matchs (1x par jour)."""
    global _tracked_matches, _last_refresh
    
    print("🔄 Rafraîchissement de la liste des matchs...")
    raw_matches = _fetch_upcoming_matches(limit=5)
    
    if raw_matches:
        _tracked_matches = [_normalize(m) for m in raw_matches]
        _last_refresh = datetime.now(LOCAL_TZ).isoformat()
        _save_cache()
        print(f"✅ Liste rafraîchie : {len(_tracked_matches)} matchs")
    else:
        print("⚠️  Impossible de rafraîchir la liste")


@app.on_event("startup")
def startup_event():
    """Au démarrage de l'API."""
    print("🚀 Démarrage de l'API LoL Matches...")
    
    # Charger le cache existant
    _load_cache()
    
    # Si pas de matchs en cache, en récupérer
    if not _tracked_matches:
        print("📥 Première récupération des matchs...")
        refresh_matches_list()
    
    # Planifier les tâches
    # Rafraîchir la liste tous les jours à 6h du matin
    scheduler.add_job(refresh_matches_list, 'cron', hour=6, minute=0, id='refresh_matches')
    
    # Mettre à jour les scores toutes les 10 minutes
    scheduler.add_job(
        update_scores, 
        'interval', 
        minutes=10, 
        id='update_scores',
        max_instances=1,
        coalesce=True,
        misfire_grace_time=60
    )
    
    scheduler.start()
    print("✅ Scheduler démarré")


@app.on_event("shutdown")
def shutdown_event():
    """À l'arrêt de l'API."""
    scheduler.shutdown()
    _save_cache()
    print("👋 API arrêtée")


@app.get("/healthz")
def healthz():
    return {
        "status": "ok",
        "tracked_matches": len(_tracked_matches),
        "last_refresh": _last_refresh
    }


@app.get("/lol/matches")
def get_matches():
    """
    Retourne les matchs trackés avec leurs scores à jour (format détaillé).
    """
    if not _tracked_matches:
        return {
            "matches": [],
            "message": "Aucun match tracké. Attendez le prochain rafraîchissement.",
            "last_refresh": _last_refresh
        }
    
    return {
        "matches": _tracked_matches,
        "last_refresh": _last_refresh,
        "next_update": "Toutes les 10 minutes"
    }


@app.get("/lol/matches/compact")
def get_matches_compact():
    """
    Format ultra-compact pour Homepage : 1 champ par match avec emojis.
    """
    if not _tracked_matches:
        return {
            "match1": "",
            "match2": "",
            "match3": "",
            "match4": "",
            "match5": "",
            "last_update": _last_refresh
        }
    
    lines = []
    for match in _tracked_matches[:5]:
        teams = match.get("teams", [])
        team1 = teams[0] if len(teams) > 0 else {}
        team2 = teams[1] if len(teams) > 1 else {}
        
        score1 = team1.get("score")
        score2 = team2.get("score")
        
        # Format du score
        if score1 is not None and score2 is not None:
            score = f"[{score1}-{score2}]"
        else:
            score = "vs"
        
        # Icons selon le statut
        status_icon = {
            "not_started": "⏰",
            "running": "🔴",
            "finished": "✅",
            "canceled": "❌",
            "postponed": "⏸️"
        }.get(match.get("status"), "📅")
        
        # Construction de la ligne
        tournament_short = match.get("tournament", "").split(" - ")[0] if match.get("tournament") else ""
        line = f"{status_icon} {team1.get('name', '?')} {score} {team2.get('name', '?')} • {match.get('begin_at_local_human', '')} • {tournament_short}"
        lines.append(line)
    
    return {
        "match1": lines[0] if len(lines) > 0 else "",
        "match2": lines[1] if len(lines) > 1 else "",
        "match3": lines[2] if len(lines) > 2 else "",
        "match4": lines[3] if len(lines) > 3 else "",
        "match5": lines[4] if len(lines) > 4 else "",
        "last_update": _last_refresh
    }


@app.post("/lol/matches/refresh")
def manual_refresh():
    """
    Force le rafraîchissement de la liste des matchs (manuel).
    """
    refresh_matches_list()
    return {
        "status": "ok",
        "message": "Liste rafraîchie",
        "matches_count": len(_tracked_matches)
    }


@app.post("/lol/matches/update-scores")
def manual_update_scores():
    """
    Force la mise à jour des scores (manuel).
    """
    update_scores()
    return {
        "status": "ok",
        "message": "Scores mis à jour",
        "matches": _tracked_matches
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)