"""
web_scraper.py — GT Scout Bot
Usa Playwright com --headless=new (modo stealth) para fazer as requisições
diretamente na sessão autenticada do Cloudflare, sem precisar do cf_clearance.
"""

import os
import asyncio
import subprocess
import threading
import logging
from datetime import datetime, timedelta

import pytz

from models import db, Match, Player, PlayerStats

logger  = logging.getLogger(__name__)
BR_TZ   = pytz.timezone("America/Sao_Paulo")

API_BASE = "https://api.gtleagues.com/api"
last_diag = {}

BROWSERS_PATH = "/opt/render/project/src/.browsers"
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = BROWSERS_PATH

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

# Lock para garantir uma sessão Playwright por vez
_pw_lock = threading.Lock()


def _ensure_browser():
    import glob
    pattern = os.path.join(BROWSERS_PATH, "chromium*", "chrome-linux", "headless_shell")
    if not glob.glob(pattern):
        logger.warning("[PW] Chromium não encontrado — instalando...")
        env = os.environ.copy()
        env["PLAYWRIGHT_BROWSERS_PATH"] = BROWSERS_PATH
        subprocess.run(
            ["python", "-m", "playwright", "install", "chromium"],
            env=env, check=True, timeout=300, capture_output=True
        )
        logger.info("[PW] Chromium instalado.")
    else:
        logger.debug("[PW] Chromium OK.")


async def _pw_fetch_json(url: str, params: dict = None) -> dict | list | None:
    """
    Abre o Playwright, visita gtleagues.com para autenticar no Cloudflare,
    depois faz fetch() da URL da API dentro da mesma página (mesma sessão).
    Retorna o JSON ou None.
    """
    from playwright.async_api import async_playwright

    if params:
        from urllib.parse import urlencode
        url = f"{url}?{urlencode(params)}"

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-features=IsolateOrigins,site-per-process",
                    "--window-size=1920,1080",
                ],
            )
            ctx = await browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent=USER_AGENT,
                locale="pt-BR",
                extra_http_headers={
                    "accept-language": "pt-BR,pt;q=0.9,en-US;q=0.8",
                },
            )
            # Esconde sinais de automação
            await ctx.add_init_script("""
                Object.defineProperty(navigator,'webdriver',{get:()=>undefined});
                Object.defineProperty(navigator,'plugins',{get:()=>[1,2,3,4,5]});
                Object.defineProperty(navigator,'languages',{get:()=>['pt-BR','pt','en-US','en']});
                window.chrome={runtime:{},loadTimes:()=>{},csi:()=>{},app:{}};
                const orig = navigator.permissions.query.bind(navigator.permissions);
                navigator.permissions.query = (p) =>
                    p.name === 'notifications'
                    ? Promise.resolve({state: Notification.permission})
                    : orig(p);
            """)

            page = await ctx.new_page()

            # 1. Visita a página principal para passar pelo Cloudflare
            logger.debug("[PW] Visitando gtleagues.com...")
            await page.goto("https://www.gtleagues.com",
                            wait_until="networkidle", timeout=60000)
            await asyncio.sleep(2)

            # 2. Faz o fetch da API dentro da mesma sessão (já autenticado no CF)
            logger.debug(f"[PW] Fetching: {url}")
            result = await page.evaluate(f"""async () => {{
                try {{
                    const r = await fetch("{url}", {{
                        method: "GET",
                        headers: {{
                            "accept": "application/json, text/plain, */*",
                            "accept-language": "pt-BR,pt;q=0.9,en-US;q=0.8",
                            "origin": "https://www.gtleagues.com",
                            "referer": "https://www.gtleagues.com/",
                        }},
                        credentials: "include",
                    }});
                    if (!r.ok) return {{ __error__: r.status }};
                    return await r.json();
                }} catch(e) {{
                    return {{ __error__: e.toString() }};
                }}
            }}""")

            await browser.close()

            if isinstance(result, dict) and "__error__" in result:
                logger.warning(f"[PW] Fetch error: {result['__error__']} — {url}")
                return None

            return result

    except Exception as e:
        logger.error(f"[PW] Erro: {e}")
        return None


def _run_pw(url, params=None):
    """Executa o Playwright numa thread isolada com lock."""
    with _pw_lock:
        _ensure_browser()
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            result = loop.run_until_complete(_pw_fetch_json(url, params))
            loop.close()
            return result
        except Exception as e:
            logger.error(f"[PW] Erro run: {e}")
            return None


def _get(path, params=None):
    url = f"{API_BASE}{path}"
    logger.debug(f"GET {url}")
    return _run_pw(url, params)


# ── Seasons ────────────────────────────────────────────────────
def get_season_ids():
    raw = os.getenv("GT_SEASON_IDS", "").strip()
    if not raw:
        logger.error("GT_SEASON_IDS não configurado!")
        return []
    return [s.strip() for s in raw.split(",") if s.strip()]


def fetch_active_seasons():
    data = _get("/seasons", {"limit": 200, "offset": 0})
    if not data:
        return []
    items  = data if isinstance(data, list) else data.get("data", [])
    cutoff = datetime.now() - timedelta(days=60)
    active = []
    for s in items:
        sid = str(s.get("id", ""))
        if not sid:
            continue
        status   = s.get("status")
        end_date = s.get("endDate") or s.get("end_date") or ""
        if status in (1, "1", "active"):
            active.append(sid)
        elif end_date:
            try:
                if datetime.fromisoformat(end_date[:10]) >= cutoff:
                    active.append(sid)
            except Exception:
                active.append(sid)
    logger.info(f"Seasons ativas: {len(active)}")
    return active


def get_seasons_to_scrape():
    env_ids  = set(get_season_ids())
    api_ids  = set(fetch_active_seasons())
    combined = (api_ids | env_ids) if api_ids else env_ids
    logger.info(f"Seasons a varrer: {len(combined)}")
    return list(combined)


def fetch_fixtures(season_id):
    data = _get(f"/seasons/{season_id}/fixtures", {"limit": 1000, "offset": 0})
    if not data:
        return []
    items = data if isinstance(data, list) else data.get("data", data.get("fixtures", []))
    if not isinstance(items, list):
        return []
    return [f for f in items if f.get("status") == 3]


def fetch_scheduled(season_id):
    data = _get(f"/seasons/{season_id}/fixtures", {"limit": 1000, "offset": 0})
    if not data:
        return []
    items = data if isinstance(data, list) else data.get("data", data.get("fixtures", []))
    if not isinstance(items, list):
        return []
    return [f for f in items if f.get("status") != 3]


def fetch_standings(season_id):
    data = _get(f"/seasons/{season_id}/standings", {"limit": 500, "offset": 0})
    if not data:
        return []
    items = data if isinstance(data, list) else data.get("data", [])
    return items if isinstance(items, list) else []


# ── Parse ──────────────────────────────────────────────────────
def parse_match(raw):
    try:
        result     = raw.get("result") or {}
        stats      = result.get("stats") or {}
        home_score = stats.get("home_score")
        away_score = stats.get("away_score")
        parts      = raw.get("participants", [])
        home_p     = next((p for p in parts if p.get("side") == "home"), None)
        away_p     = next((p for p in parts if p.get("side") == "away"), None)
        if not home_p or not away_p:
            return None

        def extract(p):
            part = p.get("participant") or {}
            pl   = part.get("player") or {}
            tm   = part.get("team")   or {}
            return {
                "player_id": str(pl.get("id", "")),
                "nickname":  (pl.get("nickname") or "").strip(),
                "team":      tm.get("name", ""),
                "crest":     tm.get("crest", ""),
                "part_id":   str(part.get("id", "")),
            }

        h  = extract(home_p);  a  = extract(away_p)
        si = raw.get("season") or {};  tr = si.get("tournament") or {}
        ca = tr.get("category") or {}; sp = ca.get("sport") or {}

        return {
            "match_id":            str(raw["id"]),
            "kickoff":             raw.get("kickoff", ""),
            "week":                raw.get("week"),
            "match_nr":            raw.get("matchNr"),
            "status":              raw.get("status"),
            "season_id":           str(raw.get("seasonId") or si.get("id") or ""),
            "season_name":         si.get("name", ""),
            "tournament_name":     tr.get("name", ""),
            "category_name":       ca.get("name", "GT Leagues"),
            "sport_name":          sp.get("name", "FC25"),
            "channel":             raw.get("channel", ""),
            "home_player_id":      h["player_id"],
            "home_nickname":       h["nickname"],
            "home_team":           h["team"],
            "home_team_crest":     h["crest"],
            "home_participant_id": h["part_id"],
            "home_score":          int(home_score) if home_score is not None else None,
            "away_player_id":      a["player_id"],
            "away_nickname":       a["nickname"],
            "away_team":           a["team"],
            "away_team_crest":     a["crest"],
            "away_participant_id": a["part_id"],
            "away_score":          int(away_score) if away_score is not None else None,
        }
    except Exception as e:
        logger.error(f"parse_match erro id={raw.get('id')}: {e}")
        return None


def upsert_match(parsed, known_ids: set) -> bool:
    mid = parsed["match_id"]
    if mid in known_ids:
        return False
    db.session.add(Match(**parsed))
    known_ids.add(mid)
    return True


def upsert_stats(raw_p, season_id):
    def _f(v):
        try: return float(v) if v is not None else 0.0
        except: return 0.0
    def _i(v):
        try: return int(v) if v is not None else 0
        except: return 0

    pid = str(raw_p.get("playerId") or raw_p.get("id") or "")
    if not pid:
        return
    nick = (raw_p.get("nickname") or "").strip()
    data = {
        "player_id": pid, "season_id": season_id, "nickname": nick,
        "team": raw_p.get("team", ""),
        "games_played":            _i(raw_p.get("games_played")),
        "points":                  _i(raw_p.get("points")),
        "wins":                    _i(raw_p.get("wins")),
        "draws":                   _i(raw_p.get("draws")),
        "losses":                  _i(raw_p.get("loses")),
        "goals_for":               _i(raw_p.get("goals_total_for")     or raw_p.get("score_total_for")),
        "goals_against":           _i(raw_p.get("goals_total_against") or raw_p.get("score_total_against")),
        "goals_diff":              _i(raw_p.get("goals_total_difference", 0)),
        "win_rate":                _f(raw_p.get("win_rate")),
        "draw_rate":               _f(raw_p.get("draw_rate")),
        "loss_rate":               _f(raw_p.get("loss_rate")),
        "goals_for_per_match":     _f(raw_p.get("goals_for_per_match")),
        "goals_against_per_match": _f(raw_p.get("goals_against_per_match")),
        "points_per_match":        _f(raw_p.get("points_per_match")),
    }
    ex = PlayerStats.query.filter_by(player_id=pid, season_id=season_id).first()
    if ex:
        for k, v in data.items():
            setattr(ex, k, v)
    else:
        db.session.add(PlayerStats(**data))
    if not Player.query.filter_by(player_id=pid).first():
        db.session.add(Player(player_id=pid, nickname=nick))


# ── Varredura principal ────────────────────────────────────────
def run_scraper():
    global last_diag
    now_str = datetime.now(BR_TZ).strftime("%d/%m/%Y %H:%M:%S")
    logger.info("=" * 55)
    logger.info(f"[{now_str}] VARREDURA — Playwright fetch in-page")
    logger.info("=" * 55)

    known_ids  = set(r[0] for r in db.session.query(Match.match_id).all())
    season_ids = get_seasons_to_scrape()
    if not season_ids:
        last_diag = {"error": "Nenhuma season configurada", "ts": now_str}
        return

    total_new = total_skip = total_play = 0
    for sid in season_ids:
        fixtures = fetch_fixtures(sid)
        new_ct   = 0
        for raw in fixtures:
            parsed = parse_match(raw)
            if parsed and upsert_match(parsed, known_ids):
                new_ct += 1
        if new_ct > 0:
            logger.info(f"  Season {sid}: +{new_ct} novas")
            for raw_p in fetch_standings(sid):
                upsert_stats(raw_p, sid)
                total_play += 1
        total_new  += new_ct
        total_skip += len(fixtures) - new_ct

    try:
        db.session.commit()
        last_diag = {
            "ts": now_str, "new": total_new, "skipped": total_skip,
            "players": total_play, "seasons": len(season_ids),
            "total_in_db": len(known_ids) + total_new,
        }
        logger.info(f"CONCLUÍDO | +{total_new} novas | {total_skip} já existiam")
    except Exception as e:
        db.session.rollback()
        last_diag = {"error": str(e), "ts": now_str}
        logger.error(f"Erro commit: {e}")


def get_last_diag():
    return last_diag
