"""
gh_coletor.py — GT Scout Bot / GitHub Actions
- Acorda o Render ANTES de qualquer coisa
- Envia em lotes de 50 partidas com retry
- Pausa entre lotes para o servidor processar
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
TIMEOUT    = 20
BATCH_SIZE = 50    # menor para evitar timeout no Render free tier

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


# ── Acorda o Render ────────────────────────────────────────────
def wake_server():
    """
    Acorda o Render fazendo várias requisições leves até responder.
    DEVE ser chamado antes de qualquer envio.
    """
    logger.info("Acordando o servidor Render (pode demorar ~30s)...")
    for attempt in range(12):  # até 120s tentando
        try:
            r = requests.get(f"{SERVER_URL}/", timeout=20)
            if r.status_code in (200, 302, 404):
                logger.info(f"✅ Servidor online! ({attempt+1} tentativa, {attempt*10}s)")
                # Aguarda mais 5s para garantir que está totalmente acordado
                time.sleep(5)
                return True
        except Exception:
            pass
        logger.info(f"  Servidor dormindo... aguardando ({attempt+1}/12)")
        time.sleep(10)
    logger.warning("⚠️ Servidor não respondeu — tentando mesmo assim.")
    return False


# ── Dados do servidor ──────────────────────────────────────────
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


def get_max_season_from_server():
    try:
        r = requests.get(f"{SERVER_URL}/api/summary", timeout=15)
        if r.status_code == 200:
            seasons = r.json().get("seasons", [])
            int_seasons = [int(s) for s in seasons if str(s).isdigit()]
            if int_seasons:
                return max(int_seasons)
    except Exception:
        pass
    return None


# ── Descoberta de seasons ──────────────────────────────────────
def discover_seasons_from_api():
    found = set()
    for offset in [0, 200, 400]:
        data = _get("/seasons", {"limit": 200, "offset": offset})
        if data:
            items = data if isinstance(data, list) else data.get("data", [])
            if isinstance(items, list):
                for s in items:
                    sid = str(s.get("id", ""))
                    if sid:
                        found.add(sid)
                if len(items) < 200:
                    break
    data2 = _get(f"/sports/{SPORT_ID}/seasons", {"limit": 100})
    if data2:
        items2 = data2 if isinstance(data2, list) else data2.get("data", [])
        for s in (items2 or []):
            sid = str(s.get("id", ""))
            if sid:
                found.add(sid)
    logger.info(f"Seasons via API: {len(found)}")
    return found


def scan_sequential_seasons(base_ids, range_before=20, range_after=300):
    if not base_ids:
        return set()
    int_ids = [int(s) for s in base_ids if str(s).isdigit()]
    if not int_ids:
        return set()
    min_id = max(1, min(int_ids) - range_before)
    max_id = max(int_ids) + range_after
    candidates = list(range(min_id, max_id + 1))
    logger.info(f"Scan paralelo: {min_id} → {max_id} ({len(candidates)} seasons, 20 threads)")

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
            result = f.result()
            if result:
                found.add(result)

    logger.info(f"Seasons com fixtures (scan): {len(found)}")
    return found


def discover_all_seasons():
    all_seasons = set()
    env_ids = {s.strip() for s in GT_SEASON_IDS.split(",") if s.strip()}
    all_seasons |= env_ids
    logger.info(f"Seasons do env: {env_ids}")

    api_seasons = discover_seasons_from_api()
    all_seasons |= api_seasons

    max_from_db = get_max_season_from_server()
    if max_from_db:
        logger.info(f"Maior season no banco: {max_from_db}")
        all_seasons.add(str(max_from_db))

    seq = scan_sequential_seasons(all_seasons.copy(), range_before=20, range_after=300)
    all_seasons |= seq

    logger.info(f"Total de seasons a verificar: {len(all_seasons)}")
    return list(all_seasons)


# ── Coleta de partidas ─────────────────────────────────────────
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


# ── Envio em lotes ─────────────────────────────────────────────
def send_batch(fixtures, standings_map, batch_num, total_batches):
    """Envia um lote com retry. Retorna True se OK."""
    sids = {str(f.get("seasonId") or "") for f in fixtures}
    all_standings = []
    for sid in sids:
        for row in standings_map.get(sid, []):
            r = dict(row)
            r["_season_id"] = sid
            all_standings.append(r)

    payload = {
        "key":       WEBHOOK_KEY,
        "fixtures":  fixtures,
        "standings": all_standings,
    }

    for attempt in range(4):
        try:
            r = requests.post(
                f"{SERVER_URL}/webhook/ingest",
                json=payload, timeout=60
            )
            if r.status_code == 200:
                d = r.json()
                logger.info(
                    f"  Lote {batch_num}/{total_batches}: "
                    f"+{d.get('new',0)} salvas | {d.get('updated',0)} já existiam"
                )
                return True

            if r.status_code in (502, 503, 504):
                logger.warning(f"  Lote {batch_num} tentativa {attempt+1}: servidor dormindo ({r.status_code}) — acordando...")
                wake_server()
            else:
                logger.warning(f"  Lote {batch_num} tentativa {attempt+1}: HTTP {r.status_code}")

        except requests.exceptions.Timeout:
            logger.warning(f"  Lote {batch_num} tentativa {attempt+1}: timeout")
        except Exception as e:
            logger.warning(f"  Lote {batch_num} tentativa {attempt+1}: {e}")

        time.sleep(8 * (attempt + 1))

    logger.error(f"  ❌ Lote {batch_num} FALHOU após 4 tentativas")
    return False


# ── Main ───────────────────────────────────────────────────────
def main():
    logger.info("=" * 55)
    logger.info("GT Scout Coletor — GitHub Actions")
    logger.info(f"Servidor: {SERVER_URL}")
    logger.info(f"Lote: {BATCH_SIZE} partidas | BATCH_SIZE configurável")
    logger.info("=" * 55)

    # PASSO 1: acorda o servidor ANTES de qualquer coisa
    wake_server()

    # PASSO 2: busca IDs conhecidos (servidor já acordado)
    known_ids = get_known_ids()

    # PASSO 3: descobre seasons
    season_ids = discover_all_seasons()
    if not season_ids:
        logger.error("Nenhuma season encontrada!")
        sys.exit(1)

    # PASSO 4: coleta partidas novas
    all_new        = []
    standings_map  = {}
    novas_por_s    = {}

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
    logger.info(f"Top seasons: {top}")
    logger.info(f"Total: {len(all_new)} partidas novas")

    if not all_new:
        logger.info("Nada novo. Concluído.")
        return

    # PASSO 5: envia em lotes pequenos
    batches      = [all_new[i:i+BATCH_SIZE] for i in range(0, len(all_new), BATCH_SIZE)]
    total_batches = len(batches)
    logger.info(f"Enviando {total_batches} lote(s) de até {BATCH_SIZE} partidas...")

    failed = 0
    total_saved = 0

    for i, batch in enumerate(batches, 1):
        ok = send_batch(batch, standings_map, i, total_batches)
        if ok:
            total_saved += len(batch)
        else:
            failed += 1
        # Pausa entre lotes para o Render processar
        if i < total_batches:
            time.sleep(3)

    logger.info(f"✅ Concluído! {total_saved} partidas enviadas em {total_batches} lote(s).")
    if failed:
        logger.error(f"❌ {failed} lote(s) falharam!")
        sys.exit(1)


if __name__ == "__main__":
    main()
