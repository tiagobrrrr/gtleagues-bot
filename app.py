import os
import io
import json
import logging
from datetime import datetime
import pytz

from flask import Flask, render_template, jsonify, request, send_file, redirect, url_for
from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import func, or_, and_

from models import db, Match, Player, PlayerStats
from web_scraper import run_scraper, get_last_diag, fetch_scheduled, get_season_ids, parse_match
from excel_exporter import (
    build_excel_reports, build_excel_stats,
    build_excel_h2h, build_excel_charts, send_excel_email
)

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BR_TZ = pytz.timezone("America/Sao_Paulo")

def now_br():
    return datetime.now(BR_TZ)

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "gtscout-dev-key")

# ── Banco ──────────────────────────────────────────────────────
DB_URL = os.getenv("DATABASE_URL", "sqlite:///gtscout.db")
DB_URL = DB_URL.replace("postgres://", "postgresql://")
if "postgresql://" in DB_URL and "+psycopg" not in DB_URL:
    DB_URL = DB_URL.replace("postgresql://", "postgresql+psycopg://")

app.config["SQLALCHEMY_DATABASE_URI"] = DB_URL
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"pool_pre_ping": True, "pool_recycle": 300}

db.init_app(app)
with app.app_context():
    db.create_all()
    logger.info("Tabelas criadas/verificadas.")

INTERVAL = int(os.getenv("SCRAPER_INTERVAL_MINUTES", 15))

# ── Scheduler ──────────────────────────────────────────────────
scheduler = BackgroundScheduler(timezone="America/Sao_Paulo")

def scraper_job():
    with app.app_context():
        run_scraper()

def weekly_email_job():
    with app.app_context():
        matches = (Match.query
                   .filter(Match.home_score.isnot(None))
                   .order_by(Match.kickoff.desc()).all())
        ok = send_excel_email(matches)
        logger.info(f"Email semanal {'enviado' if ok else 'FALHOU'}.")

weekly_day  = int(os.getenv("EMAIL_WEEKLY_DAY", 0))
weekly_hour = int(os.getenv("EMAIL_WEEKLY_HOUR", 8))
DAY_NAMES   = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

scheduler.add_job(scraper_job, "interval", minutes=INTERVAL,
                  id="gt_scraper", next_run_time=now_br())
scheduler.add_job(weekly_email_job, "cron",
                  day_of_week=DAY_NAMES[weekly_day],
                  hour=weekly_hour, minute=0, id="weekly_email")
scheduler.start()
logger.info(f"Scheduler iniciado — varredura a cada {INTERVAL} min via curl_cffi.")


# ── Helpers ────────────────────────────────────────────────────
def get_summary():
    total     = Match.query.filter(Match.home_score.isnot(None)).count()
    total_all = Match.query.count()
    today     = now_br().strftime("%Y-%m-%d")
    today_ct  = Match.query.filter(
        Match.home_score.isnot(None),
        Match.kickoff.like(f"{today}%")
    ).count()
    seasons = [r[0] for r in db.session.query(Match.season_id).distinct().all() if r[0]]
    return {"total": total, "total_all": total_all, "today": today_ct, "seasons": seasons}


def avg_goals_individual():
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


def h2h_stats(p1_nick, p2_nick):
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
        s1, s2 = (m.home_score, m.away_score) if m.home_nickname == p1_nick else (m.away_score, m.home_score)
        p1["gf"] += s1 or 0; p1["ga"] += s2 or 0
        p2["gf"] += s2 or 0; p2["ga"] += s1 or 0
        if s1 > s2:   p1["wins"]  += 1; p2["losses"] += 1; winner = p1_nick
        elif s1 < s2: p2["wins"]  += 1; p1["losses"] += 1; winner = p2_nick
        else:         p1["draws"] += 1; p2["draws"]  += 1; winner = "Empate"
        games.append({"match_id": m.match_id, "kickoff": m.kickoff,
                      "season_name": m.season_name, "p1_score": s1,
                      "p2_score": s2, "winner": winner})

    n = len(matches)
    for p in [p1, p2]:
        p["n"] = n
        p["avg_scored"]   = round(p["gf"] / n, 2) if n else 0
        p["avg_conceded"] = round(p["ga"] / n, 2) if n else 0
        p["avg_total"]    = round((p["gf"] + p["ga"]) / n, 2) if n else 0
    return {"total": n, "p1": p1, "p2": p2, "games": games}


def get_scheduled_matches():
    results = []
    for sid in get_season_ids():
        for raw in fetch_scheduled(sid):
            p = parse_match(raw)
            if p:
                results.append(p)
    results.sort(key=lambda x: x.get("kickoff") or "")
    return results


# ── Rotas ─────────────────────────────────────────────────────
@app.route("/")
def index():
    summary = get_summary()
    recent  = (Match.query.filter(Match.home_score.isnot(None))
               .order_by(Match.kickoff.desc()).limit(12).all())
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
    return render_template("scheduled.html", matches=get_scheduled_matches(), now=now_br())


@app.route("/statistics")
def statistics():
    return render_template("statistics.html", stats=avg_goals_individual())


@app.route("/players")
def players():
    all_players = Player.query.order_by(Player.nickname).all()
    return render_template("players.html", players=all_players, total=len(all_players))


@app.route("/head-to-head")
def head_to_head():
    nicknames = sorted(set(
        [r[0] for r in db.session.query(Match.home_nickname).distinct().all() if r[0]] +
        [r[0] for r in db.session.query(Match.away_nickname).distinct().all() if r[0]]
    ))
    p1 = request.args.get("p1", "")
    p2 = request.args.get("p2", "")
    result = h2h_stats(p1, p2) if (p1 and p2 and p1 != p2) else None
    return render_template("head_to_head.html", nicknames=nicknames, p1=p1, p2=p2, result=result)


@app.route("/charts")
def charts():
    stats = avg_goals_individual()
    return render_template("charts.html", stats=stats,
                           stats_json=json.dumps(stats),
                           nicknames=[s["nickname"] for s in stats])


@app.route("/reports")
def reports():
    stats = avg_goals_individual()
    return render_template("reports.html",
                           top_scorers=stats[:10],
                           most_games=sorted(stats, key=lambda x: x["games_played"], reverse=True)[:10],
                           summary=get_summary())


# ── Downloads ──────────────────────────────────────────────────
@app.route("/download/excel/reports")
def download_excel_reports():
    matches = Match.query.filter(Match.home_score.isnot(None)).order_by(Match.kickoff.desc()).all()
    if not matches:
        return "Nenhuma partida coletada.", 404
    fname = f"gtscout_partidas_{now_br().strftime('%Y-%m-%d_%H-%M')}.xlsx"
    return send_file(io.BytesIO(build_excel_reports(matches)),
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                     as_attachment=True, download_name=fname)


@app.route("/download/excel/stats")
def download_excel_stats():
    stats = avg_goals_individual()
    if not stats:
        return "Sem dados.", 404
    fname = f"gtscout_estatisticas_{now_br().strftime('%Y-%m-%d_%H-%M')}.xlsx"
    return send_file(io.BytesIO(build_excel_stats(stats)),
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                     as_attachment=True, download_name=fname)


@app.route("/download/excel/charts")
def download_excel_charts():
    stats = avg_goals_individual()
    if not stats:
        return "Sem dados.", 404
    fname = f"gtscout_graficos_{now_br().strftime('%Y-%m-%d_%H-%M')}.xlsx"
    return send_file(io.BytesIO(build_excel_charts(stats)),
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                     as_attachment=True, download_name=fname)


@app.route("/download/excel/h2h")
def download_excel_h2h():
    p1 = request.args.get("p1", "")
    p2 = request.args.get("p2", "")
    if not p1 or not p2:
        return redirect(url_for("head_to_head"))
    result = h2h_stats(p1, p2)
    if not result["games"]:
        return "Nenhum confronto encontrado.", 404
    fname = f"gtscout_h2h_{p1}_vs_{p2}.xlsx"
    return send_file(io.BytesIO(build_excel_h2h(result["games"], p1, p2, result["p1"], result["p2"])),
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                     as_attachment=True, download_name=fname)


@app.route("/download/excel")
def download_excel():
    return redirect(url_for("download_excel_reports"))


# ── API ────────────────────────────────────────────────────────
@app.route("/api/status")
def api_status():
    jobs = {job.id: str(job.next_run_time) for job in scheduler.get_jobs()}
    return jsonify({
        "status": "ok",
        "interval_minutes": INTERVAL,
        "engine": "curl_cffi Chrome TLS impersonation",
        "matches_finalized": Match.query.filter(Match.home_score.isnot(None)).count(),
        "scheduler": jobs,
        "last_scrape": get_last_diag(),
    })


@app.route("/api/stats")
def api_stats():
    return jsonify(avg_goals_individual())


@app.route("/api/known-ids")
def known_ids():
    ids = [r[0] for r in db.session.query(Match.match_id).all()]
    return jsonify({"ids": ids, "total": len(ids)})


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
        season_id = raw_p.pop("_season_id", "unknown")
        upsert_stats(raw_p, season_id)
        play_ct += 1

    try:
        db.session.commit()
        return jsonify({"ok": True, "new": new_ct, "updated": upd_ct, "players": play_ct})
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@app.route("/send-email", methods=["POST"])
def send_email_now():
    matches = Match.query.filter(Match.home_score.isnot(None)).order_by(Match.kickoff.desc()).all()
    ok = send_excel_email(matches)
    return jsonify({"ok": ok, "matches": len(matches)})


@app.route("/diagnostico")
def diagnostico():
    return jsonify({
        "bot":              "GT Scout",
        "engine":           "curl_cffi Chrome TLS impersonation",
        "db_matches":       Match.query.count(),
        "interval_minutes": INTERVAL,
        "last_scrape":      get_last_diag(),
        "season_ids":       os.getenv("GT_SEASON_IDS", "NAO CONFIGURADO"),
    })


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    port  = int(os.getenv("PORT", os.getenv("FLASK_PORT", 5000)))
    debug = os.getenv("FLASK_DEBUG", "false").lower() == "true"
    app.run(host="0.0.0.0", port=port, debug=debug)
