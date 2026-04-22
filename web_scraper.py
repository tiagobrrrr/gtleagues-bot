"""
web_scraper.py — GT Scout Bot
Usa ScraperAPI para contornar Cloudflare a partir do servidor Render.

ScraperAPI: https://scraperapi.com (plano grátis: 1000 req/mês)
Variável de ambiente: SCRAPER_API_KEY=sua_chave_aqui

Se não tiver ScraperAPI configurado, tenta requisição direta como fallback.
"""

import os
import requests
import logging
from datetime import datetime

from models import db, Match, Player, PlayerStats

logger = logging.getLogger(__name__)

API_BASE     = "https://api.gtleagues.com/api"
SCRAPER_KEY  = os.getenv("SCRAPER_API_KEY", "")
TIMEOUT      = 30
last_diag    = {}

HEADERS_DIRECT = {
    "accept": "application/json",
    "accept-language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    "origin": "https://www.gtleagues.com",
    "referer": "https://www.gtleagues.com/",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
    "sec-ch-ua": '"Chromium";v="146", "Not-A.Brand";v="24", "Google Chrome";v="146"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-site",
}


def _get(url, params=None):
    """
    Faz requisição via ScraperAPI (se configurado) ou diretamente.
    ScraperAPI roteia por IPs residenciais — passa pelo Cloudflare.
    """
    # Monta URL com params
    if params:
        from urllib.parse import urlencode
        full_url = f"{url}?{urlencode(params)}"
    else:
        full_url = url

    # Tenta via ScraperAPI
    if SCRAPER_KEY:
        try:
            scraper_url = "http://api.scraperapi.com"
            resp = requests.get(
                scraper_url,
                params={"api_key": SCRAPER_KEY, "url": full_url},
                timeout=TIMEOUT
            )
            logger.info(f"  ScraperAPI GET {full_url} → HTTP {resp.status_code}")
            if resp.status_code == 200:
                return resp.json()
            logger.warning(f"  ScraperAPI: HTTP {resp.status_code}")
        except Exception as e:
            logger.warning(f"  ScraperAPI erro: {e}")

    # Fallback: requisição direta
    try:
        resp = requests.get(full_url, headers=HEADERS_DIRECT, timeout=TIMEOUT)
        logger.info(f"  Direto GET {full_url} → HTTP {resp.status_code}")
        if resp.status_code == 200:
            return resp.json()
        logger.warning(f"  Direto: HTTP {resp.status_code}")
    except Exception as e:
        logger.error(f"  Direto erro: {e}")

    return None


def get_season_ids():
    env_ids = os.getenv("GT_SEASON_IDS", "")
    if not env_ids:
        logger.warning("GT_SEASON_IDS não configurado!")
        return []
    ids = [s.strip() for s in env_ids.split(",") if s.strip()]
    logger.info(f"  Season IDs: {ids}")
    return ids


def fetch_fixtures(season_id):
    data = _get(f"{API_BASE}/seasons/{season_id}/fixtures", {"limit": 1000, "offset": 0})
    if not data:
        return []
    items = data if isinstance(data, list) else data.get("data", data.get("fixtures", []))
    if not isinstance(items, list):
        return []
    done = [f for f in items if f.get("status") == 3]
    logger.info(f"  Season {season_id}: {len(items)} total, {len(done)} finalizadas")
    return done


def fetch_standings(season_id):
    data = _get(f"{API_BASE}/seasons/{season_id}/standings", {"limit": 1000, "offset": 0})
    if not data:
        return []
    players = data.get("data", data) if isinstance(data, dict) else data
    return players if isinstance(players, list) else []


def parse_match(raw):
    try:
        stats      = (raw.get("result") or {}).get("stats", {})
        home_score = stats.get("home_score")
        away_score = stats.get("away_score")
        parts      = raw.get("participants", [])
        home_p     = next((p for p in parts if p.get("side") == "home"), None)
        away_p     = next((p for p in parts if p.get("side") == "away"), None)
        if not home_p or not away_p:
            return None

        def ex(p):
            part = p.get("participant", {})
            pl   = part.get("player", {})
            tm   = part.get("team", {})
            return {
                "player_id": str(pl.get("id", "")),
                "nickname":  (pl.get("nickname") or "").strip(),
                "team":      tm.get("name", ""),
                "crest":     tm.get("crest", ""),
                "part_id":   str(part.get("id", "")),
            }

        h = ex(home_p)
        a = ex(away_p)
        si = raw.get("season", {})
        tr = si.get("tournament", {})
        ca = tr.get("category", {})
        sp = ca.get("sport", {})

        return {
            "match_id":          str(raw["id"]),
            "kickoff":           raw.get("kickoff", ""),
            "week":              raw.get("week"),
            "match_nr":          raw.get("matchNr"),
            "status":            raw.get("status"),
            "season_id":         str(raw.get("seasonId", si.get("id", ""))),
            "season_name":       si.get("name", ""),
            "tournament_name":   tr.get("name", ""),
            "category_name":     ca.get("name", "GT Leagues"),
            "sport_name":        sp.get("name", "FC25"),
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
        logger.error(f"  Parse error {raw.get('id')}: {e}")
        return None


def upsert_match(parsed):
    ex = Match.query.filter_by(match_id=parsed["match_id"]).first()
    if ex:
        for k, v in parsed.items():
            setattr(ex, k, v)
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

    season_id = str(raw_p.pop("_season_id", season_id))
    ex = PlayerStats.query.filter_by(player_id=pid, season_id=season_id).first()
    data = {
        "player_id": pid, "season_id": season_id,
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


def run_scraper():
    global last_diag
    import pytz
    now = datetime.now(pytz.timezone("America/Sao_Paulo")).strftime("%d/%m/%Y %H:%M:%S")
    logger.info(f"{'='*50}")
    logger.info(f"[{now}] VARREDURA GT SCOUT")
    if SCRAPER_KEY:
        logger.info(f"  Modo: ScraperAPI (bypass Cloudflare)")
    else:
        logger.info(f"  Modo: Direto (sem ScraperAPI)")
    logger.info(f"{'='*50}")

    total_new = total_upd = total_play = 0
    season_ids = get_season_ids()
    if not season_ids:
        last_diag = {"error": "GT_SEASON_IDS não configurado", "ts": now}
        return

    for sid in season_ids:
        logger.info(f"--- Season {sid} ---")
        fixtures = fetch_fixtures(sid)
        n = u = 0
        for raw in fixtures:
            p = parse_match(raw)
            if p:
                if upsert_match(p): n += 1
                else: u += 1

        standings = fetch_standings(sid)
        for raw_p in standings:
            upsert_stats(raw_p, sid)
            total_play += 1

        total_new += n; total_upd += u
        logger.info(f"  → {n} novas, {u} já existiam")

    try:
        db.session.commit()
        last_diag = {"ts": now, "new": total_new, "updated": total_upd, "players": total_play}
        logger.info(f"CONCLUÍDO: {total_new} novas | {total_upd} já existiam | {total_play} players")
    except Exception as e:
        db.session.rollback()
        last_diag = {"error": str(e), "ts": now}
        logger.error(f"Erro commit: {e}")


def get_last_diag():
    return last_diag
