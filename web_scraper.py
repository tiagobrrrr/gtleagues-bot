import requests
import logging
import os
from datetime import datetime, timezone
from models import db, Match, Player, PlayerStats

logger = logging.getLogger(__name__)

API_BASE = os.getenv("GT_API_BASE_URL", "https://www.gtleagues.com/api")
SEASON_IDS = [s.strip() for s in os.getenv("GT_SEASON_IDS", "19211").split(",")]


def fetch_results(season_id):
    """Busca resultados de partidas finalizadas (status=3) de uma temporada."""
    try:
        url = f"{API_BASE}/seasons/{season_id}/results"
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        # Aceita lista direta ou envelope {data: [...]}
        if isinstance(data, list):
            return data
        return data.get("data", [])
    except Exception as e:
        logger.error(f"Erro ao buscar resultados (season {season_id}): {e}")
        return []


def fetch_players(season_id):
    """Busca estatísticas dos players de uma temporada."""
    try:
        url = f"{API_BASE}/seasons/{season_id}/standings"
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list):
            return data
        return data.get("data", [])
    except Exception as e:
        logger.error(f"Erro ao buscar players (season {season_id}): {e}")
        return []


def parse_match(raw, season_id):
    """Converte o JSON bruto de uma partida para um dict normalizado."""
    try:
        result_stats = raw.get("result", {}).get("stats", {})
        home_score = result_stats.get("home_score")
        away_score = result_stats.get("away_score")

        home_p = next((p for p in raw.get("participants", []) if p.get("side") == "home"), None)
        away_p = next((p for p in raw.get("participants", []) if p.get("side") == "away"), None)

        if not home_p or not away_p:
            return None

        season_info = raw.get("season", {})
        tournament = season_info.get("tournament", {})
        category = tournament.get("category", {})

        return {
            "match_id": str(raw["id"]),
            "kickoff": raw.get("kickoff"),
            "week": raw.get("week"),
            "match_nr": raw.get("matchNr"),
            "status": raw.get("status"),
            "season_id": str(season_id),
            "season_name": season_info.get("name", ""),
            "tournament_name": tournament.get("name", ""),
            "category_name": category.get("name", "GT Leagues"),
            "sport_name": category.get("sport", {}).get("name", "FC25"),
            "channel": raw.get("channel", ""),

            "home_player_id": str(home_p["participant"]["player"]["id"]),
            "home_nickname": home_p["participant"]["player"]["nickname"],
            "home_team": home_p["participant"]["team"]["name"],
            "home_team_crest": home_p["participant"]["team"].get("crest", ""),
            "home_participant_id": str(home_p["participantId"]),
            "home_score": int(home_score) if home_score is not None else None,

            "away_player_id": str(away_p["participant"]["player"]["id"]),
            "away_nickname": away_p["participant"]["player"]["nickname"],
            "away_team": away_p["participant"]["team"]["name"],
            "away_team_crest": away_p["participant"]["team"].get("crest", ""),
            "away_participant_id": str(away_p["participantId"]),
            "away_score": int(away_score) if away_score is not None else None,
        }
    except Exception as e:
        logger.error(f"Erro ao parsear partida {raw.get('id')}: {e}")
        return None


def upsert_match(parsed):
    """Insere ou atualiza uma partida no banco."""
    existing = Match.query.filter_by(match_id=parsed["match_id"]).first()
    if existing:
        for k, v in parsed.items():
            setattr(existing, k, v)
    else:
        db.session.add(Match(**parsed))


def upsert_player(raw_player, season_id):
    """Insere ou atualiza estatísticas de um player."""
    pid = str(raw_player["playerId"])
    existing = PlayerStats.query.filter_by(
        player_id=pid, season_id=str(season_id)
    ).first()

    def _f(v):
        try:
            return float(v) if v is not None else 0.0
        except Exception:
            return 0.0

    def _i(v):
        try:
            return int(v) if v is not None else 0
        except Exception:
            return 0

    data = {
        "player_id": pid,
        "season_id": str(season_id),
        "nickname": raw_player.get("nickname", ""),
        "team": raw_player.get("team", ""),
        "games_played": _i(raw_player.get("games_played")),
        "points": _i(raw_player.get("points")),
        "wins": _i(raw_player.get("wins")),
        "draws": _i(raw_player.get("draws")),
        "losses": _i(raw_player.get("loses")),
        "goals_for": _i(raw_player.get("goals_total_for")),
        "goals_against": _i(raw_player.get("goals_total_against")),
        "goals_diff": _i(raw_player.get("goals_total_difference")),
        "win_rate": _f(raw_player.get("win_rate")),
        "draw_rate": _f(raw_player.get("draw_rate")),
        "loss_rate": _f(raw_player.get("loss_rate")),
        "goals_for_per_match": _f(raw_player.get("goals_for_per_match")),
        "goals_against_per_match": _f(raw_player.get("goals_against_per_match")),
        "points_per_match": _f(raw_player.get("points_per_match")),
    }

    if existing:
        for k, v in data.items():
            setattr(existing, k, v)
    else:
        db.session.add(PlayerStats(**data))

    # Garante que o player exista na tabela de players
    p = Player.query.filter_by(player_id=pid).first()
    if not p:
        db.session.add(Player(
            player_id=pid,
            nickname=raw_player.get("nickname", ""),
        ))


def run_scraper():
    """Função principal chamada pelo scheduler a cada 5 minutos."""
    logger.info(f"[{datetime.now()}] Iniciando varredura GT Leagues...")
    new_matches = 0
    new_players = 0

    for season_id in SEASON_IDS:
        # Coleta resultados
        results = fetch_results(season_id)
        for raw in results:
            if raw.get("status") == 3:  # 3 = finalizada
                parsed = parse_match(raw, season_id)
                if parsed:
                    upsert_match(parsed)
                    new_matches += 1

        # Coleta estatísticas dos players
        players = fetch_players(season_id)
        for raw_p in players:
            upsert_player(raw_p, season_id)
            new_players += 1

    try:
        db.session.commit()
        logger.info(f"Varredura concluída: {new_matches} partidas | {new_players} players atualizados.")
    except Exception as e:
        db.session.rollback()
        logger.error(f"Erro ao salvar no banco: {e}")
