import os, io, json, logging, socket
from datetime import datetime

import pytz
from flask import Flask, render_template, jsonify, request, send_file, redirect, url_for, make_response
from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler

from models import db, Match, Player, PlayerStats
from web_scraper import run_scraper, get_last_diag, fetch_scheduled, get_season_ids, parse_match
from data_analyzer import DataAnalyzer
from statistics_calculator import StatisticsCalculator
from report_generator import ReportGenerator
from email_service import EmailService
from excel_exporter import (
    build_excel_reports, build_excel_stats,
    build_excel_h2h, build_excel_charts,
    build_excel_all_h2h
)

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
BR_TZ = pytz.timezone("America/Sao_Paulo")

def now_br():
    return datetime.now(BR_TZ)

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "gtscout-dev-key")

# ── Banco: CockroachDB (10GB gratuito) ───────────────────────
DB_URL = "postgresql+psycopg2://Tiagonrr:OtIBR7YVkLCS2e7hFCXhpQ@woolly-flyer-17105.jxf.gcp-us-central1.cockroachlabs.cloud:26257/defaultdb?sslmode=require"
logger.info("Banco: CockroachDB")
logger.info(f"Banco configurado.")

app.config["SQLALCHEMY_DATABASE_URI"] = DB_URL
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"pool_pre_ping": True, "pool_recycle": 300}

db.init_app(app)
try:
    with app.app_context():
        db.create_all()
        logger.info("✅ Tabelas criadas/verificadas no Supabase.")
except Exception as e:
    logger.error(f"❌ Erro ao conectar: {e}")
    raise

analyzer   = DataAnalyzer()
calculator = StatisticsCalculator()
reporter   = ReportGenerator()
email_svc  = EmailService()

INTERVAL    = int(os.getenv("SCRAPER_INTERVAL_MINUTES", 15))
weekly_day  = int(os.getenv("EMAIL_WEEKLY_DAY", 0))
weekly_hour = int(os.getenv("EMAIL_WEEKLY_HOUR", 8))
DAY_NAMES   = ["mon","tue","wed","thu","fri","sat","sun"]

scheduler = BackgroundScheduler(timezone="America/Sao_Paulo")

def weekly_email_job():
    with app.app_context():
        xlsx, fname = reporter.generate_weekly_report()
        total = Match.query.filter(Match.home_score.isnot(None)).count()
        ok = email_svc.send_report(xlsx, fname, total)
        logger.info(f"Email semanal {'✅' if ok else '❌'}.")

scheduler.add_job(weekly_email_job, "cron",
                  day_of_week=DAY_NAMES[weekly_day],
                  hour=weekly_hour, minute=0, id="weekly_email")
scheduler.start()

@app.route("/")
def index():
    summary = analyzer.get_summary()
    recent  = Match.query.filter(Match.home_score.isnot(None)).order_by(Match.kickoff.desc()).limit(12).all()
    return render_template("index.html", summary=summary, recent=recent, now=now_br())

@app.route("/matches")
def matches():
    page   = int(request.args.get("page", 1))
    season = request.args.get("season", "")
    q = Match.query.filter(Match.home_score.isnot(None))
    if season:
        q = q.filter(Match.season_id == season)
    pagination = q.order_by(Match.kickoff.desc()).paginate(page=page, per_page=30, error_out=False)
    seasons = [r[0] for r in db.session.query(Match.season_id).distinct().all() if r[0]]
    return render_template("matches.html", pagination=pagination, seasons=seasons, selected=season)

@app.route("/scheduled")
def scheduled():
    results = []
    for sid in get_season_ids():
        for raw in fetch_scheduled(sid):
            p = parse_match(raw)
            if p: results.append(p)
    results.sort(key=lambda x: x.get("kickoff") or "")
    return render_template("scheduled.html", matches=results, now=now_br())

@app.route("/statistics")
def statistics():
    return render_template("statistics.html", stats=analyzer.avg_goals_individual())

@app.route("/players")
def players():
    all_players = Player.query.order_by(Player.nickname).all()
    return render_template("players.html", players=all_players, total=len(all_players))

@app.route("/head-to-head")
def head_to_head():
    nicknames = analyzer.get_all_nicknames()
    p1 = request.args.get("p1", "")
    p2 = request.args.get("p2", "")
    result = analyzer.h2h_stats(p1, p2) if (p1 and p2 and p1 != p2) else None
    return render_template("head_to_head.html", nicknames=nicknames, p1=p1, p2=p2, result=result)

@app.route("/charts")
def charts():
    stats = analyzer.avg_goals_individual()
    return render_template("charts.html", stats=stats, stats_json=json.dumps(stats),
                           nicknames=[s["nickname"] for s in stats])

@app.route("/reports")
def reports():
    dash = reporter.get_dashboard_data()
    return render_template("reports.html", top_scorers=dash["top_scorers"],
                           most_games=dash["most_active"], biggest_wins=dash["biggest_wins"],
                           high_scoring=dash["high_scoring"], summary=dash["summary"],
                           goals_sum=dash["goals_summary"])

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

def _xlsx_response(xlsx: bytes, fname: str):
    return send_file(io.BytesIO(xlsx), mimetype=XLSX_MIME,
                     as_attachment=True, download_name=fname)

@app.route("/download/excel/reports")
def download_excel_reports():
    try:
        matches = Match.query.filter(Match.home_score.isnot(None))                              .order_by(Match.kickoff.desc()).all()
        if not matches:
            return "Nenhuma partida coletada.", 404
        logger.info(f"Gerando Excel com {len(matches)} partidas...")
        xlsx  = build_excel_reports(matches)
        fname = f"gtscout_partidas_{now_br().strftime('%Y-%m-%d_%H-%M')}.xlsx"
        logger.info(f"Excel gerado: {len(xlsx)} bytes")
        resp = make_response(xlsx)
        resp.headers["Content-Type"]        = XLSX_MIME
        resp.headers["Content-Disposition"] = f"attachment; filename={fname}"
        resp.headers["Content-Length"]      = str(len(xlsx))
        return resp
    except Exception as e:
        logger.error(f"Erro download reports: {e}")
        return f"Erro ao gerar planilha: {e}", 500

@app.route("/download/excel/stats")
def download_excel_stats():
    try:
        xlsx, fname = reporter.generate_stats_report()
        return _xlsx_response(xlsx, fname)
    except Exception as e:
        logger.error(f"Erro download stats: {e}")
        return f"Erro ao gerar planilha: {e}", 500

@app.route("/download/excel/charts")
def download_excel_charts():
    try:
        xlsx, fname = reporter.generate_charts_report()
        return _xlsx_response(xlsx, fname)
    except Exception as e:
        logger.error(f"Erro download charts: {e}")
        return f"Erro ao gerar planilha: {e}", 500

@app.route("/download/excel/h2h")
def download_excel_h2h():
    p1 = request.args.get("p1", "")
    p2 = request.args.get("p2", "")
    if not p1 or not p2:
        return redirect(url_for("head_to_head"))
    try:
        result = reporter.generate_h2h_report(p1, p2)
        if not result:
            return "Nenhum confronto encontrado.", 404
        xlsx, fname = result
        return _xlsx_response(xlsx, fname)
    except Exception as e:
        logger.error(f"Erro download h2h: {e}")
        return f"Erro ao gerar planilha: {e}", 500

@app.route("/download/excel/all-h2h")
def download_excel_all_h2h():
    """Download de TODOS os confrontos sem precisar selecionar player."""
    try:
        matches = Match.query.filter(Match.home_score.isnot(None))                              .order_by(Match.kickoff.desc()).all()
        if not matches:
            return "Nenhuma partida coletada.", 404
        logger.info(f"Gerando Excel H2H com {len(matches)} partidas...")
        xlsx  = build_excel_all_h2h(matches)
        fname = f"gtscout_todos_confrontos_{now_br().strftime('%Y-%m-%d_%H-%M')}.xlsx"
        resp = make_response(xlsx)
        resp.headers["Content-Type"]        = XLSX_MIME
        resp.headers["Content-Disposition"] = f"attachment; filename={fname}"
        resp.headers["Content-Length"]      = str(len(xlsx))
        return resp
    except Exception as e:
        logger.error(f"Erro download all-h2h: {e}")
        return f"Erro ao gerar planilha: {e}", 500


@app.route("/download/excel")
def download_excel():
    return redirect(url_for("download_excel_reports"))

@app.route("/api/status")
def api_status():
    jobs = {job.id: str(job.next_run_time) for job in scheduler.get_jobs()}
    return jsonify({"status": "ok", "db": "Supabase", "coleta": "GitHub Actions",
                    "matches_finalized": Match.query.filter(Match.home_score.isnot(None)).count(),
                    "total_db": Match.query.count(), "scheduler": jobs, "last_scrape": get_last_diag()})

@app.route("/api/stats")
def api_stats():
    return jsonify(analyzer.avg_goals_individual())

@app.route("/api/dashboard")
def api_dashboard():
    return jsonify(reporter.get_dashboard_data())

@app.route("/api/known-ids")
def known_ids():
    ids = [r[0] for r in db.session.query(Match.match_id).all()]
    return jsonify({"ids": ids, "total": len(ids)})

@app.route("/api/summary")
def api_summary():
    return jsonify(analyzer.get_summary())

@app.route("/webhook/ingest", methods=["POST"])
def webhook_ingest():
    from web_scraper import parse_match, upsert_match, upsert_stats
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "JSON inválido"}), 400
    if data.get("key") != os.getenv("WEBHOOK_KEY", "gtscout-webhook-2026"):
        return jsonify({"error": "Chave inválida"}), 403
    known_ids_set = set(r[0] for r in db.session.query(Match.match_id).all())
    new_ct = upd_ct = play_ct = 0
    for raw in data.get("fixtures", []):
        parsed = parse_match(raw)
        if parsed:
            if upsert_match(parsed, known_ids_set): new_ct += 1
            else: upd_ct += 1
    for raw_p in data.get("standings", []):
        sid = raw_p.pop("_season_id", "unknown")
        upsert_stats(raw_p, sid)
        play_ct += 1
    try:
        db.session.commit()
        return jsonify({"ok": True, "new": new_ct, "updated": upd_ct, "players": play_ct})
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

@app.route("/send-email", methods=["POST"])
def send_email_now():
    xlsx, fname = reporter.generate_weekly_report()
    total = Match.query.filter(Match.home_score.isnot(None)).count()
    ok = email_svc.send_report(xlsx, fname, total)
    return jsonify({"ok": ok, "matches": total})

@app.route("/diagnostico")
def diagnostico():
    return jsonify({"bot": "GT Scout", "db": "Supabase", "db_matches": Match.query.count(),
                    "last_scrape": get_last_diag(), "season_ids": os.getenv("GT_SEASON_IDS", "NÃO CONFIGURADO")})

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
