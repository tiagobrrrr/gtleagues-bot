import os
import io
import logging
from datetime import datetime
from flask import Flask, render_template, jsonify, request, send_file, flash, redirect, url_for
from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import func, or_, and_
from models import db, Match, Player, PlayerStats
from web_scraper import run_scraper
from excel_exporter import build_excel, send_excel_email

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "gtscout-dev-key")

# ── Banco de Dados ─────────────────────────────────────────────
DB_URL = os.getenv("DATABASE_URL", "sqlite:///gtscout.db")
# Fix Render/Neon: garante driver postgresql://
DB_URL = DB_URL.replace("postgresql://", "postgresql+psycopg://")
app.config["SQLALCHEMY_DATABASE_URI"] = DB_URL
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "pool_pre_ping": True,
    "pool_recycle": 300,
}
db.init_app(app)

INTERVAL = int(os.getenv("SCRAPER_INTERVAL_MINUTES", 5))

# ── Scheduler ──────────────────────────────────────────────────
scheduler = BackgroundScheduler(timezone="America/Sao_Paulo")

def scraper_job():
    with app.app_context():
        run_scraper()

def weekly_email_job():
    with app.app_context():
        matches = Match.query.filter(Match.home_score.isnot(None))\
            .order_by(Match.kickoff.desc()).all()
        ok = send_excel_email(matches)
        logger.info(f"Email semanal {'enviado' if ok else 'FALHOU'}. {len(matches)} partidas.")

# Scraper a cada 5 minutos
scheduler.add_job(scraper_job, "interval", minutes=INTERVAL,
                  id="gt_scraper", next_run_time=datetime.now())

# Email toda segunda às 08:00 (configurável via env)
weekly_day  = int(os.getenv("EMAIL_WEEKLY_DAY", 0))   # 0 = segunda
weekly_hour = int(os.getenv("EMAIL_WEEKLY_HOUR", 8))
DAY_NAMES = ["mon","tue","wed","thu","fri","sat","sun"]
scheduler.add_job(weekly_email_job, "cron",
                  day_of_week=DAY_NAMES[weekly_day],
                  hour=weekly_hour, minute=0,
                  id="weekly_email")
scheduler.start()


# ── Helpers ────────────────────────────────────────────────────
def get_summary():
    total = Match.query.filter(Match.home_score.isnot(None)).count()
    today = datetime.now().strftime("%Y-%m-%d")
    today_ct = Match.query.filter(
        Match.home_score.isnot(None),
        Match.kickoff.like(f"{today}%")
    ).count()
    seasons = [r[0] for r in db.session.query(Match.season_id).distinct().all() if r[0]]
    return {"total": total, "today": today_ct, "seasons": seasons}


def avg_goals_individual():
    rows_home = db.session.query(
        Match.home_player_id, Match.home_nickname,
        func.count(Match.id),
        func.sum(Match.home_score),
        func.sum(Match.away_score),
    ).filter(Match.home_score.isnot(None)).group_by(
        Match.home_player_id, Match.home_nickname).all()

    rows_away = db.session.query(
        Match.away_player_id, Match.away_nickname,
        func.count(Match.id),
        func.sum(Match.away_score),
        func.sum(Match.home_score),
    ).filter(Match.away_score.isnot(None)).group_by(
        Match.away_player_id, Match.away_nickname).all()

    agg = {}
    for pid, nick, gp, gf, ga in rows_home:
        agg.setdefault(pid, {"nickname": nick, "gp": 0, "gf": 0, "ga": 0})
        agg[pid]["gp"] += gp or 0
        agg[pid]["gf"] += gf or 0
        agg[pid]["ga"] += ga or 0
    for pid, nick, gp, gf, ga in rows_away:
        agg.setdefault(pid, {"nickname": nick, "gp": 0, "gf": 0, "ga": 0})
        agg[pid]["gp"] += gp or 0
        agg[pid]["gf"] += gf or 0
        agg[pid]["ga"] += ga or 0

    result = []
    for pid, d in agg.items():
        gp = d["gp"]
        result.append({
            "player_id": pid, "nickname": d["nickname"],
            "games_played": gp,
            "goals_for": d["gf"], "goals_against": d["ga"],
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

    p1 = {"wins":0,"draws":0,"losses":0,"gf":0,"ga":0}
    p2 = {"wins":0,"draws":0,"losses":0,"gf":0,"ga":0}
    games = []

    for m in matches:
        if m.home_nickname == p1_nick:
            s1, s2 = m.home_score, m.away_score
            t1, t2 = m.home_team, m.away_team
            c1, c2 = m.home_team_crest, m.away_team_crest
        else:
            s1, s2 = m.away_score, m.home_score
            t1, t2 = m.away_team, m.home_team
            c1, c2 = m.away_team_crest, m.home_team_crest

        p1["gf"] += s1 or 0; p1["ga"] += s2 or 0
        p2["gf"] += s2 or 0; p2["ga"] += s1 or 0

        if s1 > s2:   p1["wins"]+=1;  p2["losses"]+=1; winner=p1_nick
        elif s1<s2:   p2["wins"]+=1;  p1["losses"]+=1; winner=p2_nick
        else:         p1["draws"]+=1; p2["draws"]+=1;  winner="Empate"

        games.append({"match_id":m.match_id,"kickoff":m.kickoff,"season_name":m.season_name,
                      "p1_team":t1,"p1_crest":c1,"p1_score":s1,
                      "p2_team":t2,"p2_crest":c2,"p2_score":s2,"winner":winner})

    n = len(matches)
    for p in [p1, p2]:
        p["avg_scored"]   = round(p["gf"]/n, 2) if n else 0
        p["avg_conceded"] = round(p["ga"]/n, 2) if n else 0
        p["avg_total"]    = round((p["gf"]+p["ga"])/n, 2) if n else 0

    return {"total": n, "p1": p1, "p2": p2, "games": games}


# ── Rotas ──────────────────────────────────────────────────────
@app.route("/")
def index():
    summary = get_summary()
    recent  = Match.query.filter(Match.home_score.isnot(None))\
        .order_by(Match.kickoff.desc()).limit(8).all()
    return render_template("index.html", summary=summary, recent=recent, now=datetime.now())


@app.route("/matches")
def matches():
    page    = int(request.args.get("page", 1))
    season  = request.args.get("season", "")
    q = Match.query.filter(Match.home_score.isnot(None))
    if season:
        q = q.filter(Match.season_id == season)
    pagination = q.order_by(Match.kickoff.desc()).paginate(page=page, per_page=30, error_out=False)
    seasons = [r[0] for r in db.session.query(Match.season_id).distinct().all() if r[0]]
    return render_template("matches.html", pagination=pagination, seasons=seasons, selected=season)


@app.route("/statistics")
def statistics():
    stats        = avg_goals_individual()
    season_stats = PlayerStats.query.order_by(PlayerStats.goals_for_per_match.desc()).all()
    return render_template("statistics.html", stats=stats, season_stats=season_stats)


@app.route("/players")
def players():
    all_stats = PlayerStats.query.order_by(PlayerStats.points.desc()).all()
    seasons   = [r[0] for r in db.session.query(PlayerStats.season_id).distinct().all() if r[0]]
    return render_template("players.html", all_stats=all_stats, seasons=seasons)


@app.route("/head-to-head")
def head_to_head():
    nicknames = sorted(set(
        [r[0] for r in db.session.query(Match.home_nickname).distinct().all() if r[0]] +
        [r[0] for r in db.session.query(Match.away_nickname).distinct().all() if r[0]]
    ))
    p1     = request.args.get("p1", "")
    p2     = request.args.get("p2", "")
    result = None
    if p1 and p2 and p1 != p2:
        result = h2h_stats(p1, p2)
    return render_template("head_to_head.html", nicknames=nicknames, p1=p1, p2=p2, result=result)


@app.route("/charts")
def charts():
    stats = avg_goals_individual()
    return render_template("charts.html", stats=stats)


@app.route("/reports")
def reports():
    stats       = avg_goals_individual()
    top_scorers = stats[:10]
    most_games  = sorted(stats, key=lambda x: x["games_played"], reverse=True)[:10]
    return render_template("reports.html", top_scorers=top_scorers,
                           most_games=most_games, summary=get_summary())


# ── Download Excel ─────────────────────────────────────────────
@app.route("/download/excel")
def download_excel():
    matches = Match.query.filter(Match.home_score.isnot(None))\
        .order_by(Match.kickoff.desc()).all()
    xlsx = build_excel(matches)
    now_str  = datetime.now().strftime("%Y-%m-%d_%H-%M")
    filename = f"gtscout_{now_str}.xlsx"
    return send_file(
        io.BytesIO(xlsx),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=filename
    )


@app.route("/download/excel/h2h")
def download_excel_h2h():
    p1 = request.args.get("p1", "")
    p2 = request.args.get("p2", "")
    if not p1 or not p2:
        return redirect(url_for("head_to_head"))
    matches = Match.query.filter(
        or_(
            and_(Match.home_nickname == p1, Match.away_nickname == p2),
            and_(Match.home_nickname == p2, Match.away_nickname == p1),
        )
    ).filter(Match.home_score.isnot(None)).order_by(Match.kickoff.desc()).all()
    xlsx = build_excel(matches)
    filename = f"gtscout_h2h_{p1}_vs_{p2}.xlsx"
    return send_file(
        io.BytesIO(xlsx),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=filename
    )


# ── Envio manual de email ──────────────────────────────────────
@app.route("/send-email", methods=["POST"])
def send_email_now():
    matches = Match.query.filter(Match.home_score.isnot(None))\
        .order_by(Match.kickoff.desc()).all()
    ok = send_excel_email(matches)
    return jsonify({"ok": ok, "matches": len(matches)})


# ── Status da API ──────────────────────────────────────────────
@app.route("/api/status")
def api_status():
    jobs = {}
    for job in scheduler.get_jobs():
        jobs[job.id] = str(job.next_run_time)
    return jsonify({
        "status": "ok",
        "matches": Match.query.filter(Match.home_score.isnot(None)).count(),
        "scheduler": jobs
    })


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    port  = int(os.getenv("PORT", os.getenv("FLASK_PORT", 5000)))
    debug = os.getenv("FLASK_DEBUG", "false").lower() == "true"
    app.run(host="0.0.0.0", port=port, debug=debug)
