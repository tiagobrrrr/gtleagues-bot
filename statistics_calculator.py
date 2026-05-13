"""
statistics_calculator.py — GT Scout Bot
Cálculos estatísticos avançados. Padrão do fifa25-bot.
"""

import logging
from models import db, Match

logger = logging.getLogger(__name__)


class StatisticsCalculator:

    def goals_summary(self, stats_list: list) -> dict:
        """Resumo geral de gols de todos os players."""
        if not stats_list:
            return {}
        total_gf  = sum(s["goals_for"]      for s in stats_list)
        total_ga  = sum(s["goals_against"]   for s in stats_list)
        total_gp  = sum(s["games_played"]    for s in stats_list)
        avg_total = round((total_gf + total_ga) / total_gp, 2) if total_gp else 0
        return {
            "total_goals_for":     total_gf,
            "total_goals_against": total_ga,
            "total_games":         total_gp,
            "avg_goals_per_match": avg_total,
        }

    def player_ranking(self, stats_list: list, key: str = "avg_goals_scored") -> list:
        """Ranking de players por critério."""
        return sorted(stats_list, key=lambda x: x.get(key, 0), reverse=True)

    def win_rate_ranking(self, stats_list: list) -> list:
        """Ranking por taxa de vitória."""
        enriched = []
        for s in stats_list:
            gp = s.get("games_played", 0)
            w  = s.get("wins", 0)
            s["win_rate_pct"] = round((w / gp * 100), 1) if gp else 0
            enriched.append(s)
        return sorted(enriched, key=lambda x: x["win_rate_pct"], reverse=True)

    def consistency_score(self, stats_list: list) -> list:
        """
        Score de consistência: penaliza quem tem poucos jogos.
        Formula: avg_goals_scored * ln(games_played + 1)
        """
        import math
        result = []
        for s in stats_list:
            gp   = s.get("games_played", 0)
            avg  = s.get("avg_goals_scored", 0)
            score = round(avg * math.log(gp + 1), 3)
            result.append({**s, "consistency_score": score})
        return sorted(result, key=lambda x: x["consistency_score"], reverse=True)

    def draw_analysis(self, stats_list: list) -> list:
        """Players com mais empates (alta taxa de empate)."""
        enriched = []
        for s in stats_list:
            gp = s.get("games_played", 0)
            d  = s.get("draws", 0)
            s["draw_rate_pct"] = round((d / gp * 100), 1) if gp else 0
            enriched.append(s)
        return sorted(enriched, key=lambda x: x["draw_rate_pct"], reverse=True)

    def high_scoring_matches(self, limit: int = 10) -> list:
        """Partidas com mais gols."""
        matches = Match.query.filter(
            Match.home_score.isnot(None),
            Match.away_score.isnot(None)
        ).all()
        scored = []
        for m in matches:
            total = (m.home_score or 0) + (m.away_score or 0)
            scored.append({
                "match_id":       m.match_id,
                "kickoff":        m.kickoff,
                "home_nickname":  m.home_nickname,
                "away_nickname":  m.away_nickname,
                "home_score":     m.home_score,
                "away_score":     m.away_score,
                "total_goals":    total,
                "season_name":    m.season_name,
            })
        return sorted(scored, key=lambda x: x["total_goals"], reverse=True)[:limit]

    def biggest_wins(self, limit: int = 10) -> list:
        """Maiores goleadas."""
        matches = Match.query.filter(
            Match.home_score.isnot(None),
            Match.away_score.isnot(None)
        ).all()
        wins = []
        for m in matches:
            diff = abs((m.home_score or 0) - (m.away_score or 0))
            if diff == 0:
                continue
            winner = m.home_nickname if m.home_score > m.away_score else m.away_nickname
            loser  = m.away_nickname if m.home_score > m.away_score else m.home_nickname
            wins.append({
                "match_id":    m.match_id,
                "kickoff":     m.kickoff,
                "winner":      winner,
                "loser":       loser,
                "home_score":  m.home_score,
                "away_score":  m.away_score,
                "diff":        diff,
                "season_name": m.season_name,
            })
        return sorted(wins, key=lambda x: x["diff"], reverse=True)[:limit]
