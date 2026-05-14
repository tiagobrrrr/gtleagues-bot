"""
gh_coletor.py — GT Scout Bot / GitHub Actions
Estratégia robusta de descoberta de seasons:
1. Tenta /seasons da API
2. Varre IDs sequenciais ao redor dos conhecidos
3. Sempre garante as seasons do env
"""

import os
import sys
import logging
from datetime import datetime, timedelta

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%d/%m/%Y %H:%M:%S"
)
logger = logging.getLogger(__name__)

SERVER_URL    = os.environ["SERVER_URL"]
WEBHOOK_KEY   = os.environ["WEBHOOK_KEY"]
GT_SEASON_IDS = os.environ.get("GT_SEASON_IDS", "19211")
SPORT_ID      = os.environ.get("GT_SPORT_ID", "6")

API_BASE = "https://api.gtleagues.com/api"
TIMEOUT  = 20

HEADERS = {
    "accept":           "application/json, text/plain, */*",
    "accept-language":  "pt-BR,pt;q=0.9,en-US;q=0.8",
    "origin":           "https://www.gtleagues.com",
    "referer":          "https://www.gtleagues.com/",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "sec-ch-ua":          '"Chromium";v="124","Google Chrome";v="124","Not-A.Brand";v="99"',
    "sec-ch-ua-mobile":   "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest":     "empty",
    "sec-fetch-mode":     "cors",
    "sec-fetch-site":     "same-site",
}


def _get(path, params=None):
    url = f"{API_BASE}{path}"
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=TIMEOUT)
        if r.status_code == 200:
            return r.json()
        if r.status_code != 404:
            logger.debug(f"HTTP {r.status_code}: {path}")
    except Exception as e:
        logger.debug(f"Erro {path}: {e}")
    return None


def get_known_ids():
    try:
        r = requests.get(f"{SERVER_URL}/api/known-ids", timeout=20)
        if r.status_code == 200:
            ids = set(str(i) for i in r.json().get("ids", []))
            logger.info(f"IDs no servidor: {len(ids)}")
            return ids
    except Exception as e:
        logger.error(f"Erro known-ids: {e}")
    return set()


def get_max_season_from_db(known_match_ids):
    """
    Descobre o maior season_id a partir dos match_ids já no banco.
    Faz uma requisição de diagnóstico ao servidor.
    """
    try:
        r = requests.get(f"{SERVER_URL}/api/summary", timeout=10)
        if r.status_code == 200:
            data = r.json()
            seasons = data.get("seasons", [])
            if seasons:
                int_seasons = []
                for s in seasons:
                    try: int_seasons.append(int(s))
                    except: pass
                if int_seasons:
                    return max(int_seasons)
    except Exception:
        pass
    return None


def discover_seasons_from_api():
    """Tenta listar seasons via API."""
    found = set()

    # Endpoint 1: /seasons genérico
    for offset in [0, 200, 400]:
        data = _get("/seasons", {"limit": 200, "offset": offset, "status": 1})
        if data:
            items = data if isinstance(data, list) else data.get("data", [])
            if isinstance(items, list):
                for s in items:
                    sid = str(s.get("id", ""))
                    if sid:
                        found.add(sid)
                logger.info(f"  /seasons offset={offset}: {len(items)} retornadas")
            if not items or len(items) < 200:
                break

    # Endpoint 2: /sports/{id}/seasons
    for status in [1, 2, None]:
        params = {"limit": 100}
        if status:
            params["status"] = status
        data = _get(f"/sports/{SPORT_ID}/seasons", params)
        if data:
            items = data if isinstance(data, list) else data.get("data", [])
            if isinstance(items, list):
                for s in items:
                    sid = str(s.get("id", ""))
                    if sid:
                        found.add(sid)

    logger.info(f"Seasons via API: {len(found)}")
    return found


def scan_sequential_seasons(base_ids, range_before=20, range_after=250):
    """
    Varre IDs sequenciais em paralelo (ThreadPoolExecutor).
    Muito mais rápido que sequencial.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    found = set()
    if not base_ids:
        return found

    int_ids = []
    for s in base_ids:
        try: int_ids.append(int(s))
        except: pass

    if not int_ids:
        return found

    min_id = max(1, min(int_ids) - range_before)
    max_id = max(int_ids) + range_after
    candidates = list(range(min_id, max_id + 1))

    logger.info(f"Scan paralelo: {min_id} → {max_id} ({len(candidates)} seasons, 20 threads)")

    def check(sid):
        data = _get(f"/seasons/{sid}/fixtures", {"limit": 1, "offset": 0})
        if data is None:
            return None
        items = data if isinstance(data, list) else data.get("data", data.get("fixtures", []))
        if isinstance(items, list) and len(items) > 0:
            return str(sid)
        return None

    with ThreadPoolExecutor(max_workers=20) as exe:
        futures = {exe.submit(check, sid): sid for sid in candidates}
        for f in as_completed(futures):
            result = f.result()
            if result:
                found.add(result)

    logger.info(f"Seasons com fixtures (scan): {len(found)} → {sorted(found)[:10]}...")
    return found


def discover_all_seasons():
    """Combina todas as estratégias de descoberta."""
    all_seasons = set()

    # 1. Seasons do env (garantia mínima)
    env_ids = {s.strip() for s in GT_SEASON_IDS.split(",") if s.strip()}
    all_seasons |= env_ids
    logger.info(f"Seasons do env: {env_ids}")

    # 2. Tenta API
    api_seasons = discover_seasons_from_api()
    all_seasons |= api_seasons

    # 3. Pega o maior season_id já no servidor
    max_from_db = get_max_season_from_db(None)
    if max_from_db:
        logger.info(f"Maior season no banco: {max_from_db}")
        all_seasons.add(str(max_from_db))

    # 4. Determina base para scan sequencial
    base = all_seasons.copy()
    if max_from_db:
        base.add(str(max_from_db))

    # 5. Scan sequencial (mais agressivo — garante pegar seasons novas)
    seq_seasons = scan_sequential_seasons(base, range_before=20, range_after=250)
    all_seasons |= seq_seasons

    logger.info(f"Total final de seasons a verificar: {len(all_seasons)}")
    return list(all_seasons)


def fetch_fixtures(season_id):
    data = _get(f"/seasons/{season_id}/fixtures", {"limit": 1000, "offset": 0})
    if not data:
        return []
    items = data if isinstance(data, list) else data.get("data", data.get("fixtures", []))
    if not isinstance(items, list):
        return []
    done = [f for f in items if f.get("status") == 3]
    return done


def fetch_standings(season_id):
    data = _get(f"/seasons/{season_id}/standings", {"limit": 500, "offset": 0})
    if not data:
        return []
    items = data if isinstance(data, list) else data.get("data", [])
    return items if isinstance(items, list) else []


def send_to_server(fixtures, standings_by_season):
    if not fixtures:
        logger.info("Nenhuma partida nova para enviar.")
        return True

    all_standings = []
    for sid, rows in standings_by_season.items():
        for row in rows:
            row["_season_id"] = sid
            all_standings.append(row)

    payload = {
        "key":       WEBHOOK_KEY,
        "fixtures":  fixtures,
        "standings": all_standings,
    }

    try:
        r = requests.post(f"{SERVER_URL}/webhook/ingest", json=payload, timeout=60)
        if r.status_code == 200:
            d = r.json()
            logger.info(
                f"✅ Servidor: +{d.get('new',0)} novas | "
                f"{d.get('updated',0)} já existiam | "
                f"{d.get('players',0)} players"
            )
            return True
        logger.error(f"Webhook {r.status_code}: {r.text[:300]}")
    except Exception as e:
        logger.error(f"Erro webhook: {e}")
    return False


def main():
    logger.info("=" * 55)
    logger.info("GT Scout Coletor — GitHub Actions")
    logger.info(f"Servidor: {SERVER_URL}")
    logger.info("=" * 55)

    known_ids  = get_known_ids()
    season_ids = discover_all_seasons()

    if not season_ids:
        logger.error("❌ Nenhuma season encontrada!")
        sys.exit(1)

    all_new        = []
    standings_by_s = {}
    novas_por_season = {}

    for sid in season_ids:
        fixtures = fetch_fixtures(sid)
        if not fixtures:
            continue
        new_fix = [f for f in fixtures if str(f.get("id", "")) not in known_ids]
        if new_fix:
            novas_por_season[sid] = len(new_fix)
            all_new.extend(new_fix)
            standings_by_s[sid] = fetch_standings(sid)

    if novas_por_season:
        logger.info(f"Partidas novas por season: {novas_por_season}")
    logger.info(f"Total: {len(all_new)} partidas novas")

    ok = send_to_server(all_new, standings_by_s)
    if not ok and all_new:
        sys.exit(1)

    logger.info("✅ Concluído.")


if __name__ == "__main__":
    main()
