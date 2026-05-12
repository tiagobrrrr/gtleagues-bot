"""
gh_coletor.py — Roda no GitHub Actions (IP não bloqueado pelo Cloudflare)
Coleta partidas finalizadas e envia ao servidor Render via webhook.
"""

import os
import json
import sys
import logging
from datetime import datetime, timedelta

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger(__name__)

SERVER_URL    = os.environ["SERVER_URL"]        # ex: https://gtleagues-bot.onrender.com
WEBHOOK_KEY   = os.environ["WEBHOOK_KEY"]       # ex: gtscout-webhook-2026
GT_SEASON_IDS = os.environ.get("GT_SEASON_IDS", "19211")

API_BASE = "https://api.gtleagues.com/api"
TIMEOUT  = 30

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
        logger.warning(f"HTTP {r.status_code}: {url}")
    except Exception as e:
        logger.error(f"Erro GET {url}: {e}")
    return None


def get_known_ids():
    """Busca IDs já salvos no servidor para evitar reenvios."""
    try:
        r = requests.get(f"{SERVER_URL}/api/known-ids", timeout=20)
        if r.status_code == 200:
            ids = set(r.json().get("ids", []))
            logger.info(f"IDs já no servidor: {len(ids)}")
            return ids
    except Exception as e:
        logger.error(f"Erro ao buscar known-ids: {e}")
    return set()


def get_seasons():
    env_ids = {s.strip() for s in GT_SEASON_IDS.split(",") if s.strip()}

    # Tenta buscar seasons ativas da API
    data = _get("/seasons", {"limit": 200, "offset": 0})
    if data:
        items  = data if isinstance(data, list) else data.get("data", [])
        cutoff = datetime.now() - timedelta(days=60)
        api_ids = set()
        for s in items:
            sid = str(s.get("id", ""))
            if not sid:
                continue
            status   = s.get("status")
            end_date = s.get("endDate") or s.get("end_date") or ""
            if status in (1, "1", "active"):
                api_ids.add(sid)
            elif end_date:
                try:
                    if datetime.fromisoformat(end_date[:10]) >= cutoff:
                        api_ids.add(sid)
                except Exception:
                    api_ids.add(sid)
        combined = api_ids | env_ids
        logger.info(f"Seasons: {len(combined)} (API: {len(api_ids)}, .env: {len(env_ids)})")
        return list(combined)

    logger.info(f"Seasons do .env: {env_ids}")
    return list(env_ids)


def fetch_fixtures(season_id):
    data = _get(f"/seasons/{season_id}/fixtures", {"limit": 1000, "offset": 0})
    if not data:
        return []
    items = data if isinstance(data, list) else data.get("data", data.get("fixtures", []))
    if not isinstance(items, list):
        return []
    done = [f for f in items if f.get("status") == 3]
    logger.info(f"  Season {season_id}: {len(items)} total, {len(done)} finalizadas")
    return done


def fetch_standings(season_id):
    data = _get(f"/seasons/{season_id}/standings", {"limit": 500, "offset": 0})
    if not data:
        return []
    items = data if isinstance(data, list) else data.get("data", [])
    return items if isinstance(items, list) else []


def send_to_server(fixtures, standings_by_season):
    if not fixtures:
        logger.info("Nada novo para enviar.")
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
        r = requests.post(f"{SERVER_URL}/webhook/ingest",
                          json=payload, timeout=60)
        if r.status_code == 200:
            d = r.json()
            logger.info(
                f"Servidor recebeu: +{d.get('new',0)} novas | "
                f"{d.get('updated',0)} atualizadas | "
                f"{d.get('players',0)} players"
            )
            return True
        logger.error(f"Webhook {r.status_code}: {r.text[:300]}")
    except Exception as e:
        logger.error(f"Erro webhook: {e}")
    return False


def main():
    logger.info("=" * 50)
    logger.info(f"GT Scout Coletor — GitHub Actions")
    logger.info(f"Servidor: {SERVER_URL}")
    logger.info("=" * 50)

    known_ids   = get_known_ids()
    season_ids  = get_seasons()

    if not season_ids:
        logger.error("Nenhuma season configurada!")
        sys.exit(1)

    all_new         = []
    standings_by_s  = {}

    for sid in season_ids:
        fixtures = fetch_fixtures(sid)
        new_fix  = [f for f in fixtures if str(f.get("id", "")) not in known_ids]
        if new_fix:
            logger.info(f"  Season {sid}: {len(new_fix)} novas para enviar")
            all_new.extend(new_fix)
            standings_by_s[sid] = fetch_standings(sid)
        else:
            logger.info(f"  Season {sid}: nada novo")

    logger.info(f"Total de novas partidas: {len(all_new)}")
    ok = send_to_server(all_new, standings_by_s)

    if not ok and all_new:
        sys.exit(1)

    logger.info("Concluído.")


if __name__ == "__main__":
    main()
