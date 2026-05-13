"""
report_generator.py — GT Scout Bot
Geração de relatórios. Padrão do fifa25-bot.
"""

import io
import logging
from datetime import datetime

import pytz

from data_analyzer import DataAnalyzer
from statistics_calculator import StatisticsCalculator
from excel_exporter import (
    build_excel_reports, build_excel_stats,
    build_excel_charts, build_excel_h2h
)
from models import Match

logger = logging.getLogger(__name__)
BR_TZ  = pytz.timezone("America/Sao_Paulo")


class ReportGenerator:

    def __init__(self):
        self.analyzer   = DataAnalyzer()
        self.calculator = StatisticsCalculator()

    def generate_weekly_report(self) -> tuple[bytes, str]:
        """
        Gera planilha semanal de partidas.
        Retorna (bytes, filename).
        """
        matches = (Match.query
                   .filter(Match.home_score.isnot(None))
                   .order_by(Match.kickoff.desc()).all())
        xlsx   = build_excel_reports(matches)
        fname  = f"gtscout_partidas_{datetime.now(BR_TZ).strftime('%Y-%m-%d')}.xlsx"
        logger.info(f"[Report] Relatório semanal gerado: {len(matches)} partidas.")
        return xlsx, fname

    def generate_stats_report(self) -> tuple[bytes, str]:
        """Gera planilha de estatísticas individuais."""
        stats = self.analyzer.avg_goals_individual()
        xlsx  = build_excel_stats(stats)
        fname = f"gtscout_estatisticas_{datetime.now(BR_TZ).strftime('%Y-%m-%d')}.xlsx"
        logger.info(f"[Report] Relatório de estatísticas gerado: {len(stats)} players.")
        return xlsx, fname

    def generate_charts_report(self) -> tuple[bytes, str]:
        """Gera planilha com dados para gráficos."""
        stats = self.analyzer.avg_goals_individual()
        xlsx  = build_excel_charts(stats)
        fname = f"gtscout_graficos_{datetime.now(BR_TZ).strftime('%Y-%m-%d')}.xlsx"
        return xlsx, fname

    def generate_h2h_report(self, p1: str, p2: str) -> tuple[bytes, str] | None:
        """Gera planilha de confronto direto entre dois players."""
        result = self.analyzer.h2h_stats(p1, p2)
        if not result["games"]:
            return None
        xlsx  = build_excel_h2h(result["games"], p1, p2, result["p1"], result["p2"])
        fname = f"gtscout_h2h_{p1}_vs_{p2}_{datetime.now(BR_TZ).strftime('%Y-%m-%d')}.xlsx"
        logger.info(f"[Report] H2H gerado: {p1} vs {p2} — {result['total']} jogos.")
        return xlsx, fname

    def get_dashboard_data(self) -> dict:
        """Dados completos para o dashboard."""
        stats     = self.analyzer.avg_goals_individual()
        summary   = self.analyzer.get_summary()
        goals_sum = self.calculator.goals_summary(stats)
        top_score = self.calculator.player_ranking(stats, "avg_goals_scored")[:5]
        top_wins  = self.calculator.win_rate_ranking(stats)[:5]
        top_games = self.calculator.player_ranking(stats, "games_played")[:5]
        big_wins  = self.calculator.biggest_wins(5)
        high_sc   = self.calculator.high_scoring_matches(5)

        return {
            "summary":       summary,
            "goals_summary": goals_sum,
            "top_scorers":   top_score,
            "top_wins":      top_wins,
            "most_active":   top_games,
            "biggest_wins":  big_wins,
            "high_scoring":  high_sc,
            "total_players": len(stats),
        }
