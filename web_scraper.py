"""
web_scraper.py — GT Scout Bot
APIs confirmadas:
  Fixtures de uma season   : GET https://api.gtleagues.com/api/seasons/{seasonId}/fixtures?limit=1000&offset=0
  Standings de uma season  : GET https://api.gtleagues.com/api/seasons/{seasonId}/standings?limit=1000&offset=0
  Detalhe de partida       : GET https://api.gtleagues.com/api/fixtures/{matchId}
  H2H fixtures             : GET https://api.gtleagues.com/api/sports/6/players/{p1}/head-to-head/{p2}/fixtures
  H2H stats                : GET https://api.gtleagues.com/api/sports/6/players/{p1}/head-to-head/{p2}/stats

Estratégia:
  1. Busca lista de seasons ativas do sport 6
  2. Para cada season, busca fixtures com status=3 (finalizada)
  3. Para cada season com partidas, busca standings
  4. Upsert tudo no banco
"""

import os
import requests
import logging
from datetime import datetime, timedelta, timezone

from models import db, Match, Player, PlayerStats

logger = logging.getLogger(__name__)

API_BASE = "https://api.gtleagues.com/api"
SPORT_ID = 6
HEADERS  = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}
TIMEOUT = 20


# ── Requisição genérica ────────────────────────────────────────
def _get(url, params=None):
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()
    except requests.exceptions.HTTPError as e:
        logger.warning(f"HTTP {r.status_code} em {url}: {e}")
        return None
    except Exception as e:
        logger.error(f"Erro GET {url}: {e}")
        return None


# ── 1. Busca seasons ativas ────────────────────────────────────
def fetch_active_seasons():
    """
    Busca temporadas ativas do sport FC25 (sport_id=6).
    Tenta vários endpoints possíveis.
    """
    # Endpoint 1: seasons por sport
    url = f"{API_BASE}/sports/{SPORT_ID}/seasons"
    data = _get(url, {"limit": 200, "offset": 0})
    seasons = _extract_list(data)
    if seasons:
        logger.info(f"Seasons via sport endpoint: {len(seasons)}")
        return seasons

    # Endpoint 2: seasons gerais filtradas por sport
    url2 = f"{API_BASE}/seasons"
    data2 = _get(url2, {"sportId": SPORT_ID, "limit": 200, "offset": 0})
    seasons2 = _extract_list(data2)
    if seasons2:
        logger.info(f"Seasons via seasons endpoint: {len(seasons2)}")
        return seasons2

    # Fallback: usa season IDs do .env
    env_ids = os.getenv("GT_SEASON_IDS", "")
    if env_ids:
        ids = [s.strip() for s in env_ids.split(",") if s.strip()]
        logger.info(f"Usando {len(ids)} season IDs do .env como fallback")
        return [{"id": sid} for sid in ids]

    return []


def _extract_list(data):
    if isinstance(data, list) and data:
        return data
    if isinstance(data, dict):
        for key in ("data", "seasons", "results", "items"):
            if key in data and isinstance(data[key], list):
                return data[key]
    return []


# ── 2. Fixtures de uma season ─────────────────────────────────
def fetch_season_fixtures(season_id):
    """
    Busca partidas de uma temporada.
    URL: /api/seasons/{seasonId}/fixtures?limit=1000&offset=0
    """
    url = f"{API_BASE}/seasons/{season_id}/fixtures"
    data = _get(url, {"limit": 1000, "offset": 0})
    result = _extract_list(data)
    # Filtra só finalizadas (status=3)
    return [f for f in result if f.get("status") == 3]


# ── 3. Standings de uma season ────────────────────────────────
def fetch_season_standings(season_id):
    """
    URL: /api/seasons/{seasonId}/standings?limit=1000&offset=0
    Retorna: {tagId, data: [...players]}
    """
    url = f"{API_BASE}/seasons/{season_id}/standings"
    data = _get(url, {"limit": 1000, "offset": 0})
    if isinstance(data, dict):
        return data.get("data", [])
    if isinstance(data, list):
        return data
    return []


# ── 4. Parse de fixture ───────────────────────────────────────
def parse_match(raw):
    """
    Converte JSON bruto de fixture para dict normalizado.
    Campos confirmados pelos exemplos reais.
    """
    try:
        # Scores
        result_stats = (raw.get("result") or {}).get("stats", {})
        home_score   = result_stats.get("home_score")
        away_score   = result_stats.get("away_score")

        # Participantes
        participants = raw.get("participants", [])
        home_p = next((p for p in participants if p.get("side") == "home"), None)
        away_p = next((p for p in participants if p.get("side") == "away"), None)

        if not home_p or not away_p:
            return None

        def extract(p):
            part   = p.get("participant", {})
            player = part.get("player", {})
            team   = part.get("team", {})
            return {
                "player_id":    str(player.get("id", "")),
                "nickname":     player.get("nickname", "").strip(),
                "team":         team.get("name", ""),
                "team_crest":   team.get("crest", ""),
                "part_id":      str(part.get("id", "")),
            }

        home = extract(home_p)
        away = extract(away_p)

        # Season e torneio
        season_info  = raw.get("season", {})
        tournament   = season_info.get("tournament", {})
        category     = tournament.get("category", {})
        sport        = category.get("sport", {})
        season_id    = str(raw.get("seasonId", season_info.get("id", "")))

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
            # Home
            "home_player_id":      home["player_id"],
            "home_nickname":       home["nickname"],
            "home_team":           home["team"],
            "home_team_crest":     home["team_crest"],
            "home_participant_id": home["part_id"],
            "home_score":          int(home_score) if home_score is not None else None,
            # Away
            "away_player_id":      away["player_id"],
            "away_nickname":       away["nickname"],
            "away_team":           away["team"],
            "away_team_crest":     away["team_crest"],
            "away_participant_id": away["part_id"],
            "away_score":          int(away_score) if away_score is not None else None,
        }
    except Exception as e:
        logger.error(f"Erro ao parsear fixture {raw.get('id')}: {e}")
        return None


# ── 5. Upserts ────────────────────────────────────────────────
def upsert_match(parsed):
    existing = Match.query.filter_by(match_id=parsed["match_id"]).first()
    if existing:
        for k, v in parsed.items():
            setattr(existing, k, v)
        return False  # atualizado
    else:
        db.session.add(Match(**parsed))
        return True   # novo


def upsert_player_stats(raw_p, season_id):
    def _f(v):
        try: return float(v) if v is not None else 0.0
        except: return 0.0
    def _i(v):
        try: return int(v) if v is not None else 0
        except: return 0

    pid = str(raw_p.get("playerId", raw_p.get("id", "")))
    if not pid:
        return

    existing = PlayerStats.query.filter_by(player_id=pid, season_id=str(season_id)).first()

    data = {
        "player_id":               pid,
        "season_id":               str(season_id),
        "nickname":                (raw_p.get("nickname") or "").strip(),
        "team":                    raw_p.get("team", ""),
        "games_played":            _i(raw_p.get("games_played")),
        "points":                  _i(raw_p.get("points")),
        "wins":                    _i(raw_p.get("wins")),
        "draws":                   _i(raw_p.get("draws")),
        "losses":                  _i(raw_p.get("loses")),
        "goals_for":               _i(raw_p.get("goals_total_for",  raw_p.get("score_total_for"))),
        "goals_against":           _i(raw_p.get("goals_total_against", raw_p.get("score_total_against"))),
        "goals_diff":              _i(raw_p.get("goals_total_difference", 0)),
        "win_rate":                _f(raw_p.get("win_rate")),
        "draw_rate":               _f(raw_p.get("draw_rate")),
        "loss_rate":               _f(raw_p.get("loss_rate")),
        "goals_for_per_match":     _f(raw_p.get("goals_for_per_match")),
        "goals_against_per_match": _f(raw_p.get("goals_against_per_match")),
        "points_per_match":        _f(raw_p.get("points_per_match")),
    }

    if existing:
        for k, v in data.items():
            setattr(existing, k, v)
    else:
        db.session.add(PlayerStats(**data))

    # Garante registro na tabela players
    if not Player.query.filter_by(player_id=pid).first():
        db.session.add(Player(player_id=pid, nickname=data["nickname"]))


# ── 6. Job principal ──────────────────────────────────────────
def run_scraper():
    import pytz
    BR_TZ = pytz.timezone("America/Sao_Paulo")
    now_br = datetime.now(BR_TZ).strftime("%H:%M:%S")
    logger.info(f"[{now_br}] ========== Iniciando varredura GT Scout ==========")

    total_new  = 0
    total_upd  = 0
    total_play = 0
    seasons_ok = 0

    # ── Etapa 1: descobre seasons ──────────────────────────────
    seasons = fetch_active_seasons()
    logger.info(f"Seasons para processar: {len(seasons)}")

    # ── Etapa 2: para cada season, busca fixtures + standings ──
    for season in seasons:
        season_id = str(season.get("id", ""))
        if not season_id:
            continue

        # Fixtures
        fixtures = fetch_season_fixtures(season_id)
        if not fixtures:
            continue

        seasons_ok += 1
        logger.info(f"Season {season_id}: {len(fixtures)} partidas finalizadas")

        new_ct = 0
        for raw in fixtures:
            parsed = parse_match(raw)
            if parsed:
                is_new = upsert_match(parsed)
                if is_new:
                    new_ct += 1
                    total_new += 1
                else:
                    total_upd += 1

        # Standings
        standings = fetch_season_standings(season_id)
        for raw_p in standings:
            upsert_player_stats(raw_p, season_id)
            total_play += 1

        if new_ct:
            logger.info(f"  → {new_ct} novas partidas salvas")

    # ── Etapa 3: commit ────────────────────────────────────────
    try:
        db.session.commit()
        logger.info(
            f"Varredura concluída: {total_new} novas | "
            f"{total_upd} atualizadas | "
            f"{total_play} players | "
            f"{seasons_ok}/{len(seasons)} seasons"
        )
    except Exception as e:
        db.session.rollback()
        logger.error(f"Erro ao salvar no banco: {e}")
