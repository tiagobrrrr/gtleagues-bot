"""
web_scraper.py — GT Scout Bot
URLs confirmadas:
  Fixtures  : GET https://api.gtleagues.com/api/seasons/{seasonId}/fixtures?limit=1000&offset=0
  Standings : GET https://api.gtleagues.com/api/seasons/{seasonId}/standings?limit=1000&offset=0
  Seasons   : GET https://api.gtleagues.com/api/sports/6/seasons
"""

import os
import requests
import logging
from datetime import datetime

from models import db, Match, Player, PlayerStats

logger = logging.getLogger(__name__)

API_BASE = "https://api.gtleagues.com/api"
SPORT_ID = 6
HEADERS  = {
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0",
    "Referer": "https://www.gtleagues.com/",
    "Origin":  "https://www.gtleagues.com",
}
TIMEOUT = 25

# Armazena resultado do último diagnóstico
last_diag = {}


# ── Requisição com log completo ────────────────────────────────
def _get(url, params=None):
    try:
        full_url = requests.Request("GET", url, params=params).prepare().url
        logger.info(f"  GET {full_url}")
        r = requests.get(url, headers=HEADERS, params=params, timeout=TIMEOUT)
        logger.info(f"  → HTTP {r.status_code} | {len(r.content)} bytes")
        if r.status_code != 200:
            logger.warning(f"  → Body: {r.text[:300]}")
            return None
        data = r.json()
        return data
    except Exception as e:
        logger.error(f"  → ERRO: {e}")
        return None


# ── Busca season IDs ──────────────────────────────────────────
def get_season_ids():
    """
    Tenta descobrir seasons automaticamente.
    Fallback para GT_SEASON_IDS do .env.
    """
    ids = []

    # Tenta endpoint de seasons por sport
    for endpoint in [
        f"{API_BASE}/sports/{SPORT_ID}/seasons",
        f"{API_BASE}/seasons?sportId={SPORT_ID}&limit=100&offset=0",
        f"{API_BASE}/sports/{SPORT_ID}/fixtures?limit=5&offset=0",  # extrai seasonId dos fixtures
    ]:
        data = _get(endpoint)
        if data:
            items = data if isinstance(data, list) else data.get("data", data.get("seasons", []))
            if isinstance(items, list) and items:
                for item in items:
                    sid = item.get("id") or item.get("seasonId")
                    if sid and str(sid) not in ids:
                        ids.append(str(sid))
                if ids:
                    logger.info(f"  Seasons descobertos via API: {ids}")
                    return ids

    # Fallback .env
    env_ids = os.getenv("GT_SEASON_IDS", "")
    if env_ids:
        ids = [s.strip() for s in env_ids.split(",") if s.strip()]
        logger.info(f"  Usando season IDs do .env: {ids}")
        return ids

    logger.warning("  NENHUM season ID encontrado!")
    return []


# ── Fixtures de uma season ────────────────────────────────────
def fetch_fixtures(season_id):
    url  = f"{API_BASE}/seasons/{season_id}/fixtures"
    data = _get(url, {"limit": 1000, "offset": 0})
    if data is None:
        return []
    items = data if isinstance(data, list) else data.get("data", data.get("fixtures", []))
    if not isinstance(items, list):
        logger.warning(f"  Formato inesperado de fixtures: {type(items)}")
        return []
    done = [f for f in items if f.get("status") == 3]
    logger.info(f"  Season {season_id}: {len(items)} fixtures totais, {len(done)} finalizadas")
    return done


# ── Standings de uma season ───────────────────────────────────
def fetch_standings(season_id):
    url  = f"{API_BASE}/seasons/{season_id}/standings"
    data = _get(url, {"limit": 1000, "offset": 0})
    if data is None:
        return []
    players = data.get("data", data) if isinstance(data, dict) else data
    if not isinstance(players, list):
        return []
    logger.info(f"  Season {season_id}: {len(players)} players nos standings")
    return players


# ── Parse de fixture ──────────────────────────────────────────
def parse_match(raw):
    try:
        stats      = (raw.get("result") or {}).get("stats", {})
        home_score = stats.get("home_score")
        away_score = stats.get("away_score")

        parts  = raw.get("participants", [])
        home_p = next((p for p in parts if p.get("side") == "home"), None)
        away_p = next((p for p in parts if p.get("side") == "away"), None)
        if not home_p or not away_p:
            return None

        def ex(p):
            part   = p.get("participant", {})
            player = part.get("player", {})
            team   = part.get("team", {})
            return {
                "player_id": str(player.get("id", "")),
                "nickname":  (player.get("nickname") or "").strip(),
                "team":      team.get("name", ""),
                "crest":     team.get("crest", ""),
                "part_id":   str(part.get("id", "")),
            }

        h = ex(home_p)
        a = ex(away_p)

        season_info = raw.get("season", {})
        tournament  = season_info.get("tournament", {})
        category    = tournament.get("category", {})
        sport       = category.get("sport", {})
        season_id   = str(raw.get("seasonId", season_info.get("id", "")))

        return {
            "match_id":          str(raw["id"]),
            "kickoff":           raw.get("kickoff", ""),
            "week":              raw.get("week"),
            "match_nr":          raw.get("matchNr"),
            "status":            raw.get("status"),
            "season_id":         season_id,
            "season_name":       season_info.get("name", ""),
            "tournament_name":   tournament.get("name", ""),
            "category_name":     category.get("name", "GT Leagues"),
            "sport_name":        sport.get("name", "FC25"),
            "channel":           raw.get("channel", ""),
            "home_player_id":    h["player_id"],
            "home_nickname":     h["nickname"],
            "home_team":         h["team"],
            "home_team_crest":   h["crest"],
            "home_participant_id": h["part_id"],
            "home_score":        int(home_score) if home_score is not None else None,
            "away_player_id":    a["player_id"],
            "away_nickname":     a["nickname"],
            "away_team":         a["team"],
            "away_team_crest":   a["crest"],
            "away_participant_id": a["part_id"],
            "away_score":        int(away_score) if away_score is not None else None,
        }
    except Exception as e:
        logger.error(f"Parse error {raw.get('id')}: {e}")
        return None


# ── Upserts ───────────────────────────────────────────────────
def upsert_match(parsed):
    ex = Match.query.filter_by(match_id=parsed["match_id"]).first()
    if ex:
        for k, v in parsed.items(): setattr(ex, k, v)
        return False
    db.session.add(Match(**parsed))
    return True


def upsert_stats(raw_p, season_id):
    def _f(v):
        try: return float(v) if v else 0.0
        except: return 0.0
    def _i(v):
        try: return int(v) if v else 0
        except: return 0

    pid = str(raw_p.get("playerId", raw_p.get("id", "")))
    if not pid: return

    ex = PlayerStats.query.filter_by(player_id=pid, season_id=str(season_id)).first()
    data = {
        "player_id": pid, "season_id": str(season_id),
        "nickname":  (raw_p.get("nickname") or "").strip(),
        "team":      raw_p.get("team", ""),
        "games_played":  _i(raw_p.get("games_played")),
        "points":        _i(raw_p.get("points")),
        "wins":          _i(raw_p.get("wins")),
        "draws":         _i(raw_p.get("draws")),
        "losses":        _i(raw_p.get("loses")),
        "goals_for":     _i(raw_p.get("goals_total_for", raw_p.get("score_total_for"))),
        "goals_against": _i(raw_p.get("goals_total_against", raw_p.get("score_total_against"))),
        "goals_diff":    _i(raw_p.get("goals_total_difference", 0)),
        "win_rate":      _f(raw_p.get("win_rate")),
        "draw_rate":     _f(raw_p.get("draw_rate")),
        "loss_rate":     _f(raw_p.get("loss_rate")),
        "goals_for_per_match":     _f(raw_p.get("goals_for_per_match")),
        "goals_against_per_match": _f(raw_p.get("goals_against_per_match")),
        "points_per_match":        _f(raw_p.get("points_per_match")),
    }
    if ex:
        for k, v in data.items(): setattr(ex, k, v)
    else:
        db.session.add(PlayerStats(**data))

    if not Player.query.filter_by(player_id=pid).first():
        db.session.add(Player(player_id=pid, nickname=data["nickname"]))


# ── Job principal ─────────────────────────────────────────────
def run_scraper():
    global last_diag
    import pytz
    now = datetime.now(pytz.timezone("America/Sao_Paulo")).strftime("%H:%M:%S")
    logger.info(f"{'='*50}")
    logger.info(f"[{now}] VARREDURA GT SCOUT")
    logger.info(f"{'='*50}")

    total_new = 0; total_upd = 0; total_play = 0

    season_ids = get_season_ids()
    if not season_ids:
        last_diag = {"error": "Nenhum season ID encontrado", "ts": now}
        return

    for sid in season_ids:
        logger.info(f"--- Season {sid} ---")
        fixtures = fetch_fixtures(sid)
        n = u = 0
        for raw in fixtures:
            p = parse_match(raw)
            if p:
                is_new = upsert_match(p)
                if is_new: n += 1
                else: u += 1

        standings = fetch_standings(sid)
        for raw_p in standings:
            upsert_stats(raw_p, sid)
            total_play += 1

        total_new += n; total_upd += u
        logger.info(f"  → {n} novas, {u} atualizadas")

    try:
        db.session.commit()
        last_diag = {
            "ts": now,
            "seasons": len(season_ids),
            "new": total_new,
            "updated": total_upd,
            "players": total_play,
        }
        logger.info(f"CONCLUÍDO: {total_new} novas | {total_upd} atualiz | {total_play} players")
    except Exception as e:
        db.session.rollback()
        last_diag = {"error": str(e), "ts": now}
        logger.error(f"Erro commit: {e}")


def get_last_diag():
    return last_diag
