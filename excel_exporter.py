"""
excel_exporter.py
Gera planilha Excel com partidas finalizadas:
  - Vencedor em VERDE
  - Perdedor em VERMELHO
  - Empate em AMARELO
"""
import os
import io
import smtplib
import logging
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email import encoders

import pytz
from openpyxl import Workbook

BR_TZ = pytz.timezone('America/Sao_Paulo')
def now_br():
    from datetime import datetime
    return datetime.now(BR_TZ)
from openpyxl.styles import (
    PatternFill, Font, Alignment, Border, Side, GradientFill
)
from openpyxl.utils import get_column_letter

logger = logging.getLogger(__name__)

# ── Cores ─────────────────────────────────────────────────────
FILL_WIN    = PatternFill("solid", fgColor="1A7A4A")   # verde escuro
FILL_LOSE   = PatternFill("solid", fgColor="8B1A1A")   # vermelho escuro
FILL_DRAW   = PatternFill("solid", fgColor="7A6A00")   # amarelo escuro
FILL_HEADER = PatternFill("solid", fgColor="0D1117")
FILL_ROW1   = PatternFill("solid", fgColor="161B22")
FILL_ROW2   = PatternFill("solid", fgColor="0D1117")

FONT_WIN    = Font(bold=True, color="00FF88", name="Calibri", size=11)
FONT_LOSE   = Font(bold=True, color="FF6B6B", name="Calibri", size=11)
FONT_DRAW   = Font(bold=True, color="FFD700", name="Calibri", size=11)
FONT_HEADER = Font(bold=True, color="00E5A0", name="Calibri", size=11)
FONT_NORMAL = Font(color="C9D1D9", name="Calibri", size=10)
FONT_MUTED  = Font(color="8B949E", name="Calibri", size=10)

ALIGN_CENTER = Alignment(horizontal="center", vertical="center")
ALIGN_LEFT   = Alignment(horizontal="left",   vertical="center")

def _thin_border():
    s = Side(style="thin", color="21262D")
    return Border(left=s, right=s, top=s, bottom=s)


def build_excel(matches) -> bytes:
    """
    Recebe lista de objetos Match e retorna bytes do arquivo .xlsx.
    """
    wb = Workbook()

    # ── Aba 1: Todas as Partidas ──────────────────────────────
    ws = wb.active
    ws.title = "Partidas"
    ws.sheet_view.showGridLines = False

    headers = [
        "#", "Data", "Semana", "Temporada",
        "Player Casa", "Time Casa", "Gols Casa",
        "Gols Visitante", "Time Visitante", "Player Visitante",
        "Resultado", "Canal"
    ]
    col_widths = [5, 18, 7, 28, 16, 18, 10, 10, 18, 16, 14, 8]

    for i, (h, w) in enumerate(zip(headers, col_widths), 1):
        cell = ws.cell(row=1, column=i, value=h)
        cell.fill = FILL_HEADER
        cell.font = FONT_HEADER
        cell.alignment = ALIGN_CENTER
        cell.border = _thin_border()
        ws.column_dimensions[get_column_letter(i)].width = w

    ws.row_dimensions[1].height = 24
    ws.freeze_panes = "A2"

    for row_idx, m in enumerate(matches, 2):
        fill_row = FILL_ROW1 if row_idx % 2 == 0 else FILL_ROW2

        # Determina resultado
        hs, as_ = m.home_score, m.away_score
        if hs is None or as_ is None:
            result_str = "—"
            fill_home = fill_row; font_home = FONT_MUTED
            fill_away = fill_row; font_away = FONT_MUTED
        elif hs > as_:
            result_str = f"{m.home_nickname} venceu"
            fill_home = FILL_WIN;  font_home = FONT_WIN
            fill_away = FILL_LOSE; font_away = FONT_LOSE
        elif hs < as_:
            result_str = f"{m.away_nickname} venceu"
            fill_home = FILL_LOSE; font_home = FONT_LOSE
            fill_away = FILL_WIN;  font_away = FONT_WIN
        else:
            result_str = "Empate"
            fill_home = FILL_DRAW; font_home = FONT_DRAW
            fill_away = FILL_DRAW; font_away = FONT_DRAW

        kickoff = ""
        if m.kickoff:
            try:
                kickoff = m.kickoff[:16].replace("T", " ")
            except Exception:
                kickoff = str(m.kickoff)

        row_data = [
            row_idx - 1,
            kickoff,
            m.week or "",
            m.season_name or "",
            m.home_nickname or "",
            m.home_team or "",
            hs if hs is not None else "",
            as_ if as_ is not None else "",
            m.away_team or "",
            m.away_nickname or "",
            result_str,
            m.channel or "",
        ]

        for col_idx, value in enumerate(row_data, 1):
            cell = ws.cell(row=row_idx, column=col_idx, value=value)
            cell.border = _thin_border()
            cell.alignment = ALIGN_CENTER

            # Colunas de player com cor de resultado
            if col_idx == 5:   # Player Casa
                cell.fill = fill_home; cell.font = font_home
            elif col_idx == 10: # Player Visitante
                cell.fill = fill_away; cell.font = font_away
            elif col_idx == 11: # Resultado
                if "venceu" in result_str:
                    cell.fill = FILL_WIN; cell.font = FONT_WIN
                elif result_str == "Empate":
                    cell.fill = FILL_DRAW; cell.font = FONT_DRAW
                else:
                    cell.fill = fill_row; cell.font = FONT_MUTED
            elif col_idx in (7, 8):  # Gols
                cell.fill = fill_row
                if col_idx == 7 and hs is not None and as_ is not None:
                    cell.font = font_home
                elif col_idx == 8 and hs is not None and as_ is not None:
                    cell.font = font_away
                else:
                    cell.font = FONT_NORMAL
            else:
                cell.fill = fill_row
                cell.font = FONT_MUTED if col_idx in (1,2,3,12) else FONT_NORMAL

        ws.row_dimensions[row_idx].height = 20

    # ── Aba 2: Estatísticas de Médias ─────────────────────────
    ws2 = wb.create_sheet("Médias de Gols")
    ws2.sheet_view.showGridLines = False

    # Calcula médias diretamente dos dados
    player_agg = {}
    for m in matches:
        if m.home_score is None:
            continue
        for nick, gf, ga in [
            (m.home_nickname, m.home_score, m.away_score),
            (m.away_nickname, m.away_score, m.home_score),
        ]:
            if not nick:
                continue
            d = player_agg.setdefault(nick, {"gp": 0, "gf": 0, "ga": 0, "w": 0, "d": 0, "l": 0})
            d["gp"] += 1
            d["gf"] += gf or 0
            d["ga"] += ga or 0
            if gf > ga: d["w"] += 1
            elif gf == ga: d["d"] += 1
            else: d["l"] += 1

    stats_sorted = sorted(
        [{"nick": k, **v} for k, v in player_agg.items()],
        key=lambda x: (x["gf"] / x["gp"] if x["gp"] else 0),
        reverse=True
    )

    h2 = ["#", "Player", "PJ", "V", "E", "D", "GF", "GC", "Saldo",
          "Méd. GF/P", "Méd. GC/P", "Méd. Total/P"]
    w2 = [5, 18, 6, 6, 6, 6, 6, 6, 8, 12, 12, 14]
    for i, (h, w) in enumerate(zip(h2, w2), 1):
        cell = ws2.cell(row=1, column=i, value=h)
        cell.fill = FILL_HEADER; cell.font = FONT_HEADER
        cell.alignment = ALIGN_CENTER; cell.border = _thin_border()
        ws2.column_dimensions[get_column_letter(i)].width = w
    ws2.row_dimensions[1].height = 24
    ws2.freeze_panes = "A2"

    for ri, s in enumerate(stats_sorted, 2):
        gp = s["gp"]
        avg_f = round(s["gf"] / gp, 2) if gp else 0
        avg_a = round(s["ga"] / gp, 2) if gp else 0
        avg_t = round((s["gf"] + s["ga"]) / gp, 2) if gp else 0
        fill = FILL_ROW1 if ri % 2 == 0 else FILL_ROW2
        row_vals = [ri-1, s["nick"], gp, s["w"], s["d"], s["l"],
                    s["gf"], s["ga"], s["gf"]-s["ga"], avg_f, avg_a, avg_t]
        for ci, val in enumerate(row_vals, 1):
            cell = ws2.cell(row=ri, column=ci, value=val)
            cell.fill = fill; cell.border = _thin_border()
            cell.alignment = ALIGN_CENTER
            if ci == 2:
                cell.font = Font(bold=True, color="E6EDF3", name="Calibri", size=10)
            elif ci in (10, 11, 12):
                color = "00FF88" if ci == 10 else ("FF6B6B" if ci == 11 else "8B949E")
                cell.font = Font(bold=True, color=color, name="Calibri", size=11)
            else:
                cell.font = FONT_MUTED
        ws2.row_dimensions[ri].height = 20

    # Serializa para bytes
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.read()


def send_excel_email(matches, recipient: str = None):
    """Envia a planilha Excel por email."""
    import os
    user     = os.getenv("EMAIL_USER", "")
    password = os.getenv("EMAIL_PASSWORD", "")
    smtp_srv = os.getenv("EMAIL_SMTP_SERVER", "smtp.gmail.com")
    smtp_port= int(os.getenv("EMAIL_SMTP_PORT", 587))
    to       = recipient or os.getenv("EMAIL_RECIPIENT", user)

    if not user or not password:
        logger.error("EMAIL_USER ou EMAIL_PASSWORD não configurados.")
        return False

    try:
        xlsx_bytes = build_excel(matches)
        now_str = now_br().strftime("%Y-%m-%d")
        filename = f"gtscout_partidas_{now_str}.xlsx"

        msg = MIMEMultipart()
        msg["From"]    = user
        msg["To"]      = to
        msg["Subject"] = f"📊 GT Scout — Relatório Semanal de Partidas ({now_str})"

        body = f"""
Olá!

Segue em anexo o relatório completo de partidas da GT Leagues gerado pelo GT Scout Bot.

📅 Data de geração: {now_br().strftime('%d/%m/%Y às %H:%M')}
⚽ Total de partidas: {len(matches)}

A planilha contém:
  • Aba "Partidas" — histórico completo com vencedores em VERDE, perdedores em VERMELHO e empates em AMARELO
  • Aba "Médias de Gols" — estatísticas individuais de cada player

GT Scout Bot 🤖
        """.strip()

        msg.attach(MIMEText(body, "plain"))

        part = MIMEBase("application", "octet-stream")
        part.set_payload(xlsx_bytes)
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", f'attachment; filename="{filename}"')
        msg.attach(part)

        with smtplib.SMTP(smtp_srv, smtp_port) as server:
            server.ehlo()
            server.starttls()
            server.login(user, password)
            server.sendmail(user, to, msg.as_string())

        logger.info(f"Email enviado para {to} com {len(matches)} partidas.")
        return True

    except Exception as e:
        logger.error(f"Erro ao enviar email: {e}")
        return False
