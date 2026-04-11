"""
web_scraper.py — GT Scout Bot
URLs reais confirmadas da API GT Leagues:
  - Partidas do dia/recentes : GET /api/sports/6/players/{p1}/head-to-head/{p2}/fixtures
  - Resultado individual     : GET /api/fixtures/{matchId}
  - Standings (stats)        : GET /api/seasons/{seasonId}/standings?limit=1000&offset=0
  - H2H stats                : GET /api/sports/6/players/{p1}/head-to-head/{p2}/stats

Estratégia de coleta:
  1. Busca lista de partidas recentes por sport (sport_id=6)
  2. Filtra status=3 (finalizada)
  3. Upsert no banco
  4. Busca standings das seasons encontradas
"""

import requests
import logging
import os
from datetime import datetime, timedelta, timezone

from models import db, Match, Player, PlayerStats

logger = logging.getLogger(__name__)

API_BASE = "https://api.gtleagues.com/api"
SPORT_ID = 6          # FC25
HEADERS  = {"Accept": "application/json", "User-Agent": "GTScoutBot/1.0"}
TIMEOUT  = 20


# ── Requisição genérica ────────────────────────────────────────
def _get(url, params=None):
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.error(f"Erro GET {url}: {e}")
        return None


# ── 1. Partidas recentes do sport ─────────────────────────────
def fetch_recent_fixtures(days_back=2):
    """
    Busca partidas finalizadas (status=3) das últimas `days_back` horas.
    Usa o endpoint de fixtures por sport com filtro de data.
    """
    url = f"{API_BASE}/sports/{SPORT_ID}/fixtures"
    # Tenta com filtro de data
    since = (datetime.now(timezone.utc) - timedelta(days=days_back)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    params = {"limit": 1000, "offset": 0, "status": 3}
    data = _get(url, params)

    # Se retornar lista direta
    if isinstance(data, list):
        return data
    # Se vier envelope {data: [...]}
    if isinstance(data, dict):
        return data.get("data", data.get("fixtures", data.get("results", [])))
    return []


def fetch_fixtures_by_season(season_id):
    """Busca todas as partidas de uma temporada específica."""
    url = f"{API_BASE}/seasons/{season_id}/fixtures"
    data = _get(url, {"limit": 1000, "offset": 0})
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("data", data.get("fixtures", []))
    return []


def fetch_fixture_detail(match_id):
    """Busca detalhes de uma partida pelo ID."""
    url = f"{API_BASE}/fixtures/{match_id}"
    return _get(url)


def fetch_standings(season_id):
    """Busca standings (estatísticas) de uma temporada."""
    url = f"{API_BASE}/seasons/{season_id}/standings"
    data = _get(url, {"limit": 1000, "offset": 0})
    if isinstance(data, dict):
        return data.get("data", [])
    if isinstance(data, list):
        return data
    return []


# ── 2. Parser de partida ───────────────────────────────────────
def parse_match(raw):
    """Converte JSON bruto de partida para dict normalizado."""
    try:
        result_stats = (raw.get("result") or {}).get("stats", {})
        home_score   = result_stats.get("home_score")
        away_score   = result_stats.get("away_score")

        participants = raw.get("participants", [])
        home_p = next((p for p in participants if p.get("side") == "home"), None)
        away_p = next((p for p in participants if p.get("side") == "away"), None)

        if not home_p or not away_p:
            return None

        # Player e time da casa
        home_part   = home_p.get("participant", {})
        home_player = home_part.get("player", {})
        home_team   = home_part.get("team", {})

        # Player e time visitante
        away_part   = away_p.get("participant", {})
        away_player = away_part.get("player", {})
        away_team   = away_part.get("team", {})

        season_info  = raw.get("season", {})
        tournament   = season_info.get("tournament", {})
        category     = tournament.get("category", {})
        sport        = category.get("sport", {})

        season_id = str(raw.get("seasonId", season_info.get("id", "")))

        return {
            "match_id":        str(raw["id"]),
            "kickoff":         raw.get("kickoff", ""),
            "week":            raw.get("week"),
            "match_nr":        raw.get("matchNr"),
            "status":          raw.get("status"),
            "season_id":       season_id,
            "season_name":     season_info.get("name", ""),
            "tournament_name": tournament.get("name", ""),
            "category_name":   category.get("name", "GT Leagues"),
            "sport_name":      sport.get("name", "FC25"),
            "channel":         raw.get("channel", ""),

            "home_player_id":    str(home_player.get("id", "")),
            "home_nickname":     home_player.get("nickname", ""),
            "home_team":         home_team.get("name", ""),
            "home_team_crest":   home_team.get("crest", ""),
            "home_participant_id": str(home_part.get("id", "")),
            "home_score":        int(home_score) if home_score is not None else None,

            "away_player_id":    str(away_player.get("id", "")),
            "away_nickname":     away_player.get("nickname", ""),
            "away_team":         away_team.get("name", ""),
            "away_team_crest":   away_team.get("crest", ""),
            "away_participant_id": str(away_part.get("id", "")),
            "away_score":        int(away_score) if away_score is not None else None,
        }
    except Exception as e:
        logger.error(f"Erro ao parsear partida {raw.get('id')}: {e}")
        return None


# ── 3. Upserts ────────────────────────────────────────────────
def upsert_match(parsed):
    existing = Match.query.filter_by(match_id=parsed["match_id"]).first()
    if existing:
        for k, v in parsed.items():
            setattr(existing, k, v)
    else:
        db.session.add(Match(**parsed))


def upsert_player_stats(raw_player, season_id):
    def _f(v):
        try: return float(v) if v is not None else 0.0
        except: return 0.0
    def _i(v):
        try: return int(v) if v is not None else 0
        except: return 0

    pid = str(raw_player.get("playerId", raw_player.get("id", "")))
    if not pid:
        return

    existing = PlayerStats.query.filter_by(
        player_id=pid, season_id=str(season_id)
    ).first()

    data = {
        "player_id":             pid,
        "season_id":             str(season_id),
        "nickname":              raw_player.get("nickname", ""),
        "team":                  raw_player.get("team", ""),
        "games_played":          _i(raw_player.get("games_played")),
        "points":                _i(raw_player.get("points")),
        "wins":                  _i(raw_player.get("wins")),
        "draws":                 _i(raw_player.get("draws")),
        "losses":                _i(raw_player.get("loses")),
        "goals_for":             _i(raw_player.get("goals_total_for",  raw_player.get("score_total_for"))),
        "goals_against":         _i(raw_player.get("goals_total_against", raw_player.get("score_total_against"))),
        "goals_diff":            _i(raw_player.get("goals_total_difference", 0)),
        "win_rate":              _f(raw_player.get("win_rate")),
        "draw_rate":             _f(raw_player.get("draw_rate")),
        "loss_rate":             _f(raw_player.get("loss_rate")),
        "goals_for_per_match":   _f(raw_player.get("goals_for_per_match")),
        "goals_against_per_match": _f(raw_player.get("goals_against_per_match")),
        "points_per_match":      _f(raw_player.get("points_per_match")),
    }

    if existing:
        for k, v in data.items():
            setattr(existing, k, v)
    else:
        db.session.add(PlayerStats(**data))

    # Garante registro na tabela players
    p = Player.query.filter_by(player_id=pid).first()
    if not p:
        db.session.add(Player(
            player_id=pid,
            nickname=raw_player.get("nickname", ""),
        ))


# ── 4. Job principal ──────────────────────────────────────────
def run_scraper():
    logger.info(f"[{datetime.now().strftime('%H:%M:%S')}] Iniciando varredura GT Scout...")
    new_matches  = 0
    upd_matches  = 0
    seasons_seen = set()

    # ── Estratégia A: endpoint de fixtures por sport ──────────
    fixtures = fetch_recent_fixtures(days_back=3)

    # ── Estratégia B: fallback — busca por season IDs configurados ──
    if not fixtures:
        logger.warning("Endpoint de sport não retornou dados. Tentando por season IDs...")
        season_ids_env = os.getenv("GT_SEASON_IDS", "")
        for sid in [s.strip() for s in season_ids_env.split(",") if s.strip()]:
            fixtures += fetch_fixtures_by_season(sid)

    # Processa partidas
    for raw in fixtures:
        if raw.get("status") != 3:
            continue
        parsed = parse_match(raw)
        if not parsed:
            continue

        existing = Match.query.filter_by(match_id=parsed["match_id"]).first()
        upsert_match(parsed)
        if existing:
            upd_matches += 1
        else:
            new_matches += 1

        if parsed["season_id"]:
            seasons_seen.add(parsed["season_id"])

    # ── Coleta standings das seasons encontradas ───────────────
    updated_players = 0
    for season_id in seasons_seen:
        standings = fetch_standings(season_id)
        for raw_p in standings:
            upsert_player_stats(raw_p, season_id)
            updated_players += 1

    try:
        db.session.commit()
        logger.info(
            f"Varredura concluída: {new_matches} novas | "
            f"{upd_matches} atualizadas | "
            f"{updated_players} players | "
            f"{len(seasons_seen)} seasons"
        )
    except Exception as e:
        db.session.rollback()
        logger.error(f"Erro ao salvar no banco: {e}")
