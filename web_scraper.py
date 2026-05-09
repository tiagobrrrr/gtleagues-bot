"""
web_scraper.py — GT Scout Bot
Playwright async abre Chrome real, resolve Cloudflare, extrai cf_clearance.
curl_cffi usa esse cookie para todas as chamadas à API.
"""

import os
import asyncio
import subprocess
import threading
import logging
from datetime import datetime, timedelta

import pytz
from curl_cffi import requests as cffi_requests

from models import db, Match, Player, PlayerStats

logger  = logging.getLogger(__name__)
BR_TZ   = pytz.timezone("America/Sao_Paulo")

API_BASE = "https://api.gtleagues.com/api"
TIMEOUT  = 30
last_diag = {}

# Caminho do Chromium — igual ao do build.sh
BROWSERS_PATH = os.getenv(
    "PLAYWRIGHT_BROWSERS_PATH",
    "/opt/render/project/src/.browsers"
)
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = BROWSERS_PATH

# ── Estado global do cookie Cloudflare ────────────────────────
_cf_state = {"cf_clearance": None, "expires": None}
_cf_lock  = threading.Lock()

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


def _ensure_browser_installed():
    """Instala o Chromium em runtime se não existir (fallback seguro)."""
    import glob
    pattern = os.path.join(BROWSERS_PATH, "chromium*", "chrome-linux", "headless_shell")
    found   = glob.glob(pattern)
    if not found:
        logger.warning(f"[CF] Chromium não encontrado em {BROWSERS_PATH} — instalando...")
        try:
            env = os.environ.copy()
            env["PLAYWRIGHT_BROWSERS_PATH"] = BROWSERS_PATH
            subprocess.run(
                ["python", "-m", "playwright", "install", "chromium"],
                env=env, check=True, timeout=300,
                capture_output=True
            )
            logger.info("[CF] Chromium instalado com sucesso em runtime.")
        except Exception as e:
            logger.error(f"[CF] Falha ao instalar Chromium em runtime: {e}")
    else:
        logger.info(f"[CF] Chromium encontrado: {found[0]}")


# ── Playwright async ───────────────────────────────────────────
async def _fetch_cf_cookie_async():
    from playwright.async_api import async_playwright
    logger.info("[CF] Abrindo Chromium para resolver Cloudflare...")
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
                    "--window-size=1920,1080",
                ],
            )
            ctx = await browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent=USER_AGENT,
                locale="pt-BR",
                extra_http_headers={"accept-language": "pt-BR,pt;q=0.9,en-US;q=0.8"},
            )
            await ctx.add_init_script("""
                Object.defineProperty(navigator,'webdriver',{get:()=>undefined});
                Object.defineProperty(navigator,'plugins',{get:()=>[1,2,3,4,5]});
                Object.defineProperty(navigator,'languages',{get:()=>['pt-BR','pt','en-US']});
                window.chrome={runtime:{}};
            """)
            page = await ctx.new_page()
            await page.goto("https://www.gtleagues.com",
                            wait_until="domcontentloaded", timeout=60000)

            cf_value = None
            for _ in range(30):
                cookies = await ctx.cookies()
                cf = next((c for c in cookies if c["name"] == "cf_clearance"), None)
                if cf:
                    cf_value = cf["value"]
                    break
                await asyncio.sleep(1)

            await browser.close()

        if cf_value:
            logger.info("[CF] cf_clearance obtido com sucesso!")
        else:
            logger.warning("[CF] cf_clearance não encontrado.")
        return cf_value

    except Exception as e:
        logger.error(f"[CF] Erro Playwright: {e}")
        return None


def _refresh_cf_cookie():
    _ensure_browser_installed()
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        cf_value = loop.run_until_complete(_fetch_cf_cookie_async())
        loop.close()
    except Exception as e:
        logger.error(f"[CF] Erro asyncio: {e}")
        cf_value = None

    with _cf_lock:
        _cf_state["cf_clearance"] = cf_value
        _cf_state["expires"] = datetime.now() + (
            timedelta(hours=4) if cf_value else timedelta(minutes=20)
        )


def _ensure_cf_cookie():
    with _cf_lock:
        expires = _cf_state.get("expires")
        needs   = expires is None or datetime.now() >= expires
    if needs:
        _refresh_cf_cookie()


# ── HTTP ───────────────────────────────────────────────────────
def _get(path, params=None):
    _ensure_cf_cookie()
    url = f"{API_BASE}{path}"

    with _cf_lock:
        cf_val = _cf_state.get("cf_clearance")

    headers = {
        "accept":           "application/json, text/plain, */*",
        "accept-language":  "pt-BR,pt;q=0.9,en-US;q=0.8",
        "origin":           "https://www.gtleagues.com",
        "referer":          "https://www.gtleagues.com/",
        "user-agent":       USER_AGENT,
        "sec-ch-ua":          '"Chromium";v="120","Google Chrome";v="120","Not-A.Brand";v="99"',
        "sec-ch-ua-mobile":   "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest":     "empty",
        "sec-fetch-mode":     "cors",
        "sec-fetch-site":     "same-site",
    }
    cookies = {"cf_clearance": cf_val} if cf_val else {}

    try:
        resp = cffi_requests.get(
            url, headers=headers, cookies=cookies,
            params=params, timeout=TIMEOUT, impersonate="chrome120",
        )
        logger.debug(f"GET {url} → {resp.status_code}")
        if resp.status_code == 200:
            return resp.json()
        if resp.status_code in (403, 503):
            logger.warning(f"[CF] Bloqueado ({resp.status_code}) — renovando cookie")
            with _cf_lock:
                _cf_state["expires"] = None
        logger.warning(f"HTTP {resp.status_code}: {url}")
    except Exception as e:
        logger.error(f"Erro GET {url}: {e}")
    return None


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
    logger.info(f"[{now_str}] VARREDURA — Playwright + curl_cffi")
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
        with _cf_lock:
            cf_ok = "ok" if _cf_state.get("cf_clearance") else "ausente"
        last_diag = {
            "ts": now_str, "new": total_new, "skipped": total_skip,
            "players": total_play, "seasons": len(season_ids),
            "total_in_db": len(known_ids) + total_new,
            "cf_cookie": cf_ok, "browsers_path": BROWSERS_PATH,
        }
        logger.info(f"CONCLUÍDO | +{total_new} novas | {total_skip} já existiam | cf={cf_ok}")
    except Exception as e:
        db.session.rollback()
        last_diag = {"error": str(e), "ts": now_str}
        logger.error(f"Erro commit: {e}")


def get_last_diag():
    return last_diag
