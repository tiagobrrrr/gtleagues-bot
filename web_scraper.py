"""
web_scraper.py — GT Scout Bot (servidor Render)
A coleta é feita pelo GitHub Actions (gh_coletor.py).
Este módulo apenas processa os dados recebidos via webhook
e fornece funções utilitárias usadas pelo app.py.
"""

import os
import logging
from datetime import datetime

import pytz

from models import db, Match, Player, PlayerStats

logger = logging.getLogger(__name__)
BR_TZ  = pytz.timezone("America/Sao_Paulo")

last_diag = {}


def get_season_ids():
    raw = os.getenv("GT_SEASON_IDS", "").strip()
    if not raw:
        return []
    return [s.strip() for s in raw.split(",") if s.strip()]


def fetch_scheduled(season_id):
    """
    Tentativa de buscar agendadas diretamente.
    No Render pode falhar por CF — retorna lista vazia nesse caso.
    """
    try:
        import requests
        headers = {
            "accept":          "application/json",
            "accept-language": "pt-BR,pt;q=0.9",
            "origin":          "https://www.gtleagues.com",
            "referer":         "https://www.gtleagues.com/",
            "user-agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0 Safari/537.36",
        }
        r = requests.get(
            f"https://api.gtleagues.com/api/seasons/{season_id}/fixtures",
            headers=headers, params={"limit": 200, "offset": 0}, timeout=15
        )
        if r.status_code == 200:
            data  = r.json()
            items = data if isinstance(data, list) else data.get("data", data.get("fixtures", []))
            return [f for f in items if isinstance(items, list) and f.get("status") != 3]
    except Exception:
        pass
    return []


def parse_match(raw):
    """
    Parser baseado na estrutura real da API GT Leagues (campos em inglês).
    Estrutura confirmada pelos arquivos de exemplo do usuário.
    """
    try:
        result     = raw.get("result") or {}
        stats      = result.get("stats") or {}
        home_score = stats.get("home_score")
        away_score = stats.get("away_score")

        parts  = raw.get("participants", [])
        home_p = next((p for p in parts if p.get("side") == "home"), None)
        away_p = next((p for p in parts if p.get("side") == "away"), None)
        if not home_p or not away_p:
            return None

        def extract(p):
            part = p.get("participant") or {}
            pl   = part.get("player")   or {}
            tm   = part.get("team")     or {}
            return {
                "player_id": str(pl.get("id", "")),
                "nickname":  (pl.get("nickname") or "").strip(),
                "team":      tm.get("name", ""),
                "crest":     tm.get("crest", ""),
                "part_id":   str(part.get("id", "")),
            }

        h  = extract(home_p);  a  = extract(away_p)
        si = raw.get("season")     or {}
        tr = si.get("tournament")  or {}
        ca = tr.get("category")    or {}
        sp = ca.get("sport")       or {}

        return {
            "match_id":            str(raw["id"]),
            "kickoff":             raw.get("kickoff", ""),
            "week":                raw.get("week"),
            "match_nr":            raw.get("matchNr"),
            "status":              raw.get("status"),
            "season_id":           str(raw.get("seasonId") or si.get("id") or ""),
            "season_name":         si.get("name", ""),
            "tournament_name":     tr.get("name", ""),
            "category_name":       ca.get("name", "GT Leagues"),
            "sport_name":          sp.get("name", "FC25"),
            "channel":             raw.get("channel", ""),
            "home_player_id":      h["player_id"],
            "home_nickname":       h["nickname"],
            "home_team":           h["team"],
            "home_team_crest":     h["crest"],
            "home_participant_id": h["part_id"],
            "home_score":          int(home_score) if home_score is not None else None,
            "away_player_id":      a["player_id"],
            "away_nickname":       a["nickname"],
            "away_team":           a["team"],
            "away_team_crest":     a["crest"],
            "away_participant_id": a["part_id"],
            "away_score":          int(away_score) if away_score is not None else None,
        }
    except Exception as e:
        logger.error(f"parse_match erro id={raw.get('id')}: {e}")
        return None


def upsert_match(parsed, known_ids: set) -> bool:
    mid = parsed["match_id"]
    if mid in known_ids:
        return False
    db.session.add(Match(**parsed))
    known_ids.add(mid)
    return True


def upsert_stats(raw_p, season_id):
    def _f(v):
        try: return float(v) if v is not None else 0.0
        except: return 0.0
    def _i(v):
        try: return int(v) if v is not None else 0
        except: return 0

    pid  = str(raw_p.get("playerId") or raw_p.get("id") or "")
    if not pid:
        return
    nick = (raw_p.get("nickname") or "").strip()

    data = {
        "player_id":               pid,
        "season_id":               season_id,
        "nickname":                nick,
        "team":                    raw_p.get("team", ""),
        "games_played":            _i(raw_p.get("games_played")),
        "points":                  _i(raw_p.get("points")),
        "wins":                    _i(raw_p.get("wins")),
        "draws":                   _i(raw_p.get("draws")),
        "losses":                  _i(raw_p.get("loses")),
        "goals_for":               _i(raw_p.get("goals_total_for")     or raw_p.get("score_total_for")),
        "goals_against":           _i(raw_p.get("goals_total_against") or raw_p.get("score_total_against")),
        "goals_diff":              _i(raw_p.get("goals_total_difference", 0)),
        "win_rate":                _f(raw_p.get("win_rate")),
        "draw_rate":               _f(raw_p.get("draw_rate")),
        "loss_rate":               _f(raw_p.get("loss_rate")),
        "goals_for_per_match":     _f(raw_p.get("goals_for_per_match")),
        "goals_against_per_match": _f(raw_p.get("goals_against_per_match")),
        "points_per_match":        _f(raw_p.get("points_per_match")),
    }

    ex = PlayerStats.query.filter_by(player_id=pid, season_id=season_id).first()
    if ex:
        for k, v in data.items():
            setattr(ex, k, v)
    else:
        db.session.add(PlayerStats(**data))

    if not Player.query.filter_by(player_id=pid).first():
        db.session.add(Player(player_id=pid, nickname=nick))


def run_scraper():
    """
    No servidor Render: apenas registra que a coleta é feita
    pelo GitHub Actions. O webhook /webhook/ingest processa os dados.
    """
    global last_diag
    now_str = datetime.now(BR_TZ).strftime("%d/%m/%Y %H:%M:%S")
    last_diag = {
        "ts":     now_str,
        "status": "Coleta feita pelo GitHub Actions (gh_coletor.py)",
        "total":  Match.query.count(),
    }
    logger.info(f"[{now_str}] Servidor pronto. Coleta via GitHub Actions.")


def get_last_diag():
    return last_diag
