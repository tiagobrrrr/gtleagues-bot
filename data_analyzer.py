"""
data_analyzer.py — GT Scout Bot
Análise de dados: estatísticas individuais, H2H, resumo geral.
Padrão extraído do fifa25-bot.
"""

import logging
from sqlalchemy import func, or_, and_
from models import db, Match

logger = logging.getLogger(__name__)


class DataAnalyzer:
    """Analisa partidas e calcula estatísticas dos players."""

    def get_summary(self):
        """Resumo geral do banco."""
        from datetime import datetime
        import pytz
        now = datetime.now(pytz.timezone("America/Sao_Paulo"))
        today = now.strftime("%Y-%m-%d")

        total     = Match.query.filter(Match.home_score.isnot(None)).count()
        total_all = Match.query.count()
        today_ct  = Match.query.filter(
            Match.home_score.isnot(None),
            Match.kickoff.like(f"{today}%")
        ).count()
        seasons = [r[0] for r in db.session.query(Match.season_id).distinct().all() if r[0]]
        return {
            "total":     total,
            "total_all": total_all,
            "today":     today_ct,
            "seasons":   seasons,
        }

    def avg_goals_individual(self):
        """Estatísticas individuais calculadas das partidas coletadas."""
        rows_home = db.session.query(
            Match.home_player_id, Match.home_nickname,
            func.count(Match.id),
            func.sum(Match.home_score),
            func.sum(Match.away_score),
            func.sum(func.cast(Match.home_score > Match.away_score, db.Integer)),
            func.sum(func.cast(Match.home_score == Match.away_score, db.Integer)),
            func.sum(func.cast(Match.home_score < Match.away_score, db.Integer)),
        ).filter(Match.home_score.isnot(None)).group_by(
            Match.home_player_id, Match.home_nickname).all()

        rows_away = db.session.query(
            Match.away_player_id, Match.away_nickname,
            func.count(Match.id),
            func.sum(Match.away_score),
            func.sum(Match.home_score),
            func.sum(func.cast(Match.away_score > Match.home_score, db.Integer)),
            func.sum(func.cast(Match.away_score == Match.home_score, db.Integer)),
            func.sum(func.cast(Match.away_score < Match.home_score, db.Integer)),
        ).filter(Match.away_score.isnot(None)).group_by(
            Match.away_player_id, Match.away_nickname).all()

        agg = {}
        for pid, nick, gp, gf, ga, w, d, l in rows_home:
            agg.setdefault(pid, {"nickname": nick, "gp": 0, "gf": 0, "ga": 0, "w": 0, "d": 0, "l": 0})
            agg[pid]["gp"] += gp or 0; agg[pid]["gf"] += gf or 0; agg[pid]["ga"] += ga or 0
            agg[pid]["w"]  += w  or 0; agg[pid]["d"]  += d  or 0; agg[pid]["l"]  += l  or 0

        for pid, nick, gp, gf, ga, w, d, l in rows_away:
            agg.setdefault(pid, {"nickname": nick, "gp": 0, "gf": 0, "ga": 0, "w": 0, "d": 0, "l": 0})
            agg[pid]["gp"] += gp or 0; agg[pid]["gf"] += gf or 0; agg[pid]["ga"] += ga or 0
            agg[pid]["w"]  += w  or 0; agg[pid]["d"]  += d  or 0; agg[pid]["l"]  += l  or 0

        result = []
        for pid, d in agg.items():
            gp = d["gp"]
            result.append({
                "player_id":          pid,
                "nickname":           d["nickname"],
                "games_played":       gp,
                "goals_for":          d["gf"],
                "goals_against":      d["ga"],
                "goals_diff":         d["gf"] - d["ga"],
                "wins":               d["w"],
                "draws":              d["d"],
                "losses":             d["l"],
                "avg_goals_scored":   round(d["gf"] / gp, 2) if gp else 0,
                "avg_goals_conceded": round(d["ga"] / gp, 2) if gp else 0,
                "avg_total_goals":    round((d["gf"] + d["ga"]) / gp, 2) if gp else 0,
            })
        return sorted(result, key=lambda x: x["avg_goals_scored"], reverse=True)

    def h2h_stats(self, p1_nick, p2_nick):
        """Confronto direto entre dois players."""
        matches = Match.query.filter(
            or_(
                and_(Match.home_nickname == p1_nick, Match.away_nickname == p2_nick),
                and_(Match.home_nickname == p2_nick, Match.away_nickname == p1_nick),
            )
        ).filter(Match.home_score.isnot(None)).order_by(Match.kickoff.desc()).all()

        p1 = {"wins": 0, "draws": 0, "losses": 0, "gf": 0, "ga": 0}
        p2 = {"wins": 0, "draws": 0, "losses": 0, "gf": 0, "ga": 0}
        games = []

        for m in matches:
            s1, s2 = (m.home_score, m.away_score) if m.home_nickname == p1_nick \
                     else (m.away_score, m.home_score)
            p1["gf"] += s1 or 0; p1["ga"] += s2 or 0
            p2["gf"] += s2 or 0; p2["ga"] += s1 or 0
            if s1 > s2:   p1["wins"]  += 1; p2["losses"] += 1; winner = p1_nick
            elif s1 < s2: p2["wins"]  += 1; p1["losses"] += 1; winner = p2_nick
            else:         p1["draws"] += 1; p2["draws"]  += 1; winner = "Empate"
            games.append({
                "match_id":    m.match_id,
                "kickoff":     m.kickoff,
                "season_name": m.season_name,
                "p1_score":    s1,
                "p2_score":    s2,
                "winner":      winner,
            })

        n = len(matches)
        for p in [p1, p2]:
            p["n"]            = n
            p["avg_scored"]   = round(p["gf"] / n, 2) if n else 0
            p["avg_conceded"] = round(p["ga"] / n, 2) if n else 0
            p["avg_total"]    = round((p["gf"] + p["ga"]) / n, 2) if n else 0

        return {"total": n, "p1": p1, "p2": p2, "games": games}

    def get_all_nicknames(self):
        """Lista de nicknames únicos para autocomplete."""
        home = [r[0] for r in db.session.query(Match.home_nickname).distinct().all() if r[0]]
        away = [r[0] for r in db.session.query(Match.away_nickname).distinct().all() if r[0]]
        return sorted(set(home + away))

    def get_top_scorers(self, limit=10):
        stats = self.avg_goals_individual()
        return stats[:limit]

    def get_most_games(self, limit=10):
        stats = self.avg_goals_individual()
        return sorted(stats, key=lambda x: x["games_played"], reverse=True)[:limit]
