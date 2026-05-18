"""
gh_coletor.py — GT Scout Bot / GitHub Actions
- Descobre seasons de forma inteligente (não varre do ID 1)
- Envia em lotes de 50 para evitar timeout no Render
- Scan limitado: ±200 IDs ao redor do maior conhecido
"""

import os
import sys
import time
import logging
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

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

API_BASE   = "https://api.gtleagues.com/api"
TIMEOUT    = 15
BATCH_SIZE = 50

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
    except Exception:
        pass
    return None


def wake_server():
    logger.info("Acordando servidor Render...")
    for attempt in range(8):
        try:
            r = requests.get(f"{SERVER_URL}/", timeout=20)
            if r.status_code in (200, 302, 404):
                logger.info(f"✅ Servidor online! ({attempt+1} tentativa)")
                time.sleep(3)
                return True
        except Exception:
            pass
        time.sleep(10)
    logger.warning("Servidor pode não responder — continuando mesmo assim.")
    return False


def get_known_ids():
    try:
        r = requests.get(f"{SERVER_URL}/api/known-ids", timeout=30)
        if r.status_code == 200:
            ids = set(str(i) for i in r.json().get("ids", []))
            logger.info(f"IDs no servidor: {len(ids)}")
            return ids
    except Exception as e:
        logger.error(f"Erro known-ids: {e}")
    return set()


def get_server_summary():
    """Retorna seasons e maior season_id do banco."""
    try:
        r = requests.get(f"{SERVER_URL}/api/summary", timeout=15)
        if r.status_code == 200:
            data    = r.json()
            seasons = data.get("seasons", [])
            int_s   = [int(s) for s in seasons if str(s).isdigit()]
            return max(int_s) if int_s else None
    except Exception:
        pass
    return None


def discover_seasons_from_api():
    """Busca seasons ativas/recentes via API."""
    found = set()
    for offset in [0, 200, 400]:
        data = _get("/seasons", {"limit": 200, "offset": offset, "status": 1})
        if data:
            items = data if isinstance(data, list) else data.get("data", [])
            if isinstance(items, list):
                for s in items:
                    sid = str(s.get("id", ""))
                    if sid:
                        found.add(sid)
                if len(items) < 200:
                    break
    # Tenta também por esporte
    data2 = _get(f"/sports/{SPORT_ID}/seasons", {"limit": 100, "status": 1})
    if data2:
        items2 = data2 if isinstance(data2, list) else data2.get("data", [])
        for s in (items2 or []):
            sid = str(s.get("id", ""))
            if sid:
                found.add(sid)
    logger.info(f"Seasons via API: {len(found)}")
    return found


def scan_around(center_id, before=200, after=100):
    """
    Scan paralelo de IDs ao redor de um centro.
    Varre apenas before IDs antes + after IDs depois.
    Máximo de 300 seasons — completa em ~30s com 20 threads.
    """
    min_id = max(1, center_id - before)
    max_id = center_id + after
    candidates = list(range(min_id, max_id + 1))
    logger.info(f"Scan: {min_id} → {max_id} ({len(candidates)} seasons, 20 threads)")

    found = set()

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
            r = f.result()
            if r:
                found.add(r)

    logger.info(f"Seasons com fixtures (scan): {len(found)}")
    return found


def discover_all_seasons():
    """Descobre seasons de forma eficiente e limitada."""
    all_seasons = set()

    # 1. Env (garantia mínima)
    env_ids = {s.strip() for s in GT_SEASON_IDS.split(",") if s.strip()}
    all_seasons |= env_ids
    logger.info(f"Seasons do env: {env_ids}")

    # 2. API
    api_seasons = discover_seasons_from_api()
    all_seasons |= api_seasons

    # 3. Maior season já no servidor
    max_db = get_server_summary()
    if max_db:
        logger.info(f"Maior season no servidor: {max_db}")
        all_seasons.add(str(max_db))

    # 4. Scan LIMITADO ao redor do maior ID conhecido
    int_ids = [int(s) for s in all_seasons if str(s).isdigit()]
    if int_ids:
        center = max(int_ids)
        scanned = scan_around(center, before=200, after=100)
        all_seasons |= scanned

    logger.info(f"Total seasons a verificar: {len(all_seasons)}")
    return list(all_seasons)


def fetch_fixtures(season_id):
    data = _get(f"/seasons/{season_id}/fixtures", {"limit": 1000, "offset": 0})
    if not data:
        return []
    items = data if isinstance(data, list) else data.get("data", data.get("fixtures", []))
    if not isinstance(items, list):
        return []
    return [f for f in items if f.get("status") == 3]


def fetch_standings(season_id):
    data = _get(f"/seasons/{season_id}/standings", {"limit": 500, "offset": 0})
    if not data:
        return []
    items = data if isinstance(data, list) else data.get("data", [])
    return items if isinstance(items, list) else []


def send_batch(fixtures, standings_map, batch_num, total):
    sids = {str(f.get("seasonId") or "") for f in fixtures}
    standings = []
    for sid in sids:
        for row in standings_map.get(sid, []):
            r = dict(row)
            r["_season_id"] = sid
            standings.append(r)

    payload = {"key": WEBHOOK_KEY, "fixtures": fixtures, "standings": standings}

    for attempt in range(4):
        try:
            r = requests.post(f"{SERVER_URL}/webhook/ingest", json=payload, timeout=60)
            if r.status_code == 200:
                d = r.json()
                logger.info(f"  Lote {batch_num}/{total}: +{d.get('new',0)} salvas | {d.get('updated',0)} já existiam")
                return True
            if r.status_code in (502, 503, 504):
                logger.warning(f"  Lote {batch_num} tentativa {attempt+1}: servidor dormindo ({r.status_code})")
                wake_server()
            else:
                logger.warning(f"  Lote {batch_num} tentativa {attempt+1}: HTTP {r.status_code}")
        except Exception as e:
            logger.warning(f"  Lote {batch_num} tentativa {attempt+1}: {e}")
        time.sleep(8 * (attempt + 1))

    logger.error(f"  ❌ Lote {batch_num} falhou após 4 tentativas")
    return False


def main():
    logger.info("=" * 55)
    logger.info("GT Scout Coletor — GitHub Actions")
    logger.info(f"Servidor: {SERVER_URL}")
    logger.info(f"Lote: {BATCH_SIZE} partidas por envio")
    logger.info("=" * 55)

    wake_server()
    known_ids  = get_known_ids()
    season_ids = discover_all_seasons()

    if not season_ids:
        logger.error("Nenhuma season encontrada!")
        sys.exit(1)

    all_new       = []
    standings_map = {}
    novas_por_s   = {}

    for sid in season_ids:
        fixtures = fetch_fixtures(sid)
        if not fixtures:
            continue
        new_fix = [f for f in fixtures if str(f.get("id", "")) not in known_ids]
        if new_fix:
            novas_por_s[sid] = len(new_fix)
            all_new.extend(new_fix)
            standings_map[sid] = fetch_standings(sid)

    top = sorted(novas_por_s.items(), key=lambda x: x[1], reverse=True)[:5]
    logger.info(f"Top seasons com novas: {top}")
    logger.info(f"Total: {len(all_new)} partidas novas")

    if not all_new:
        logger.info("Nada novo. Concluído.")
        return

    batches = [all_new[i:i+BATCH_SIZE] for i in range(0, len(all_new), BATCH_SIZE)]
    total   = len(batches)
    logger.info(f"Enviando {total} lote(s)...")

    failed = 0
    for i, batch in enumerate(batches, 1):
        ok = send_batch(batch, standings_map, i, total)
        if not ok:
            failed += 1
        if i < total:
            time.sleep(2)

    logger.info(f"✅ Concluído! {len(all_new)} partidas processadas em {total} lote(s).")
    if failed:
        logger.error(f"❌ {failed} lote(s) falharam!")
        sys.exit(1)


if __name__ == "__main__":
    main()
