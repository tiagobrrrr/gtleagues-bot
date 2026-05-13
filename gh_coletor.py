"""
gh_coletor.py — Roda no GitHub Actions (IP não bloqueado pelo Cloudflare)
Descobre seasons ativas, coleta partidas finalizadas e envia ao Render via webhook.
"""

import os
import sys
import json
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
GT_SEASON_IDS = os.environ.get("GT_SEASON_IDS", "")
SPORT_ID      = os.environ.get("GT_SPORT_ID", "6")   # 6 = FC25

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
    """IDs já no servidor — evita reenvio."""
    try:
        r = requests.get(f"{SERVER_URL}/api/known-ids", timeout=20)
        if r.status_code == 200:
            ids = set(str(i) for i in r.json().get("ids", []))
            logger.info(f"IDs já no servidor: {len(ids)}")
            return ids
    except Exception as e:
        logger.error(f"Erro ao buscar known-ids: {e}")
    return set()


def discover_seasons():
    """
    Descobre seasons ativas de múltiplas formas:
    1. Via /seasons (lista geral)
    2. Via /sports/{id}/seasons (por esporte)
    3. Fallback: IDs do env GT_SEASON_IDS
    """
    found = set()

    # Env como garantia
    if GT_SEASON_IDS:
        for s in GT_SEASON_IDS.split(","):
            s = s.strip()
            if s:
                found.add(s)
        logger.info(f"Seasons do env: {found}")

    # Tenta /seasons com paginação
    for offset in range(0, 600, 200):
        data = _get("/seasons", {"limit": 200, "offset": offset})
        if not data:
            break
        items = data if isinstance(data, list) else data.get("data", [])
        if not items:
            break

        cutoff = datetime.now() - timedelta(days=30)
        for s in items:
            sid      = str(s.get("id", ""))
            if not sid:
                continue
            status   = s.get("status")
            end_date = s.get("endDate") or s.get("end_date") or ""
            start    = s.get("startDate") or s.get("start_date") or ""

            # Ativa: status 1 ou 2, ou iniciada nos últimos 30 dias
            if status in (1, 2, "1", "2", "active", "running"):
                found.add(sid)
                continue

            # Encerrada há menos de 30 dias (pode ter partidas pendentes)
            if end_date:
                try:
                    ed = datetime.fromisoformat(end_date[:10])
                    if ed >= cutoff:
                        found.add(sid)
                        continue
                except Exception:
                    pass

            # Iniciada nos últimos 30 dias
            if start:
                try:
                    sd = datetime.fromisoformat(start[:10])
                    if sd >= cutoff:
                        found.add(sid)
                except Exception:
                    pass

        if len(items) < 200:
            break

    # Tenta /sports/{id}/seasons
    data2 = _get(f"/sports/{SPORT_ID}/seasons", {"limit": 100, "offset": 0, "status": 1})
    if data2:
        items2 = data2 if isinstance(data2, list) else data2.get("data", [])
        for s in (items2 or []):
            sid = str(s.get("id", ""))
            if sid:
                found.add(sid)

    logger.info(f"Total de seasons a varrer: {len(found)} → {sorted(found)}")
    return list(found)


def fetch_fixtures(season_id):
    """Partidas finalizadas (status == 3)."""
    data = _get(f"/seasons/{season_id}/fixtures", {"limit": 1000, "offset": 0})
    if not data:
        return []
    items = data if isinstance(data, list) else data.get("data", data.get("fixtures", []))
    if not isinstance(items, list):
        return []
    done = [f for f in items if f.get("status") == 3]
    if done:
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
        r = requests.post(
            f"{SERVER_URL}/webhook/ingest",
            json=payload, timeout=60
        )
        if r.status_code == 200:
            d = r.json()
            logger.info(
                f"✅ Servidor recebeu: +{d.get('new',0)} novas | "
                f"{d.get('updated',0)} já existiam | "
                f"{d.get('players',0)} players atualizados"
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
    logger.info(f"Sport ID: {SPORT_ID}")
    logger.info("=" * 55)

    known_ids  = get_known_ids()
    season_ids = discover_seasons()

    if not season_ids:
        logger.error("❌ Nenhuma season encontrada!")
        sys.exit(1)

    all_new        = []
    standings_by_s = {}
    seasons_vazias = 0

    for sid in season_ids:
        fixtures = fetch_fixtures(sid)
        new_fix  = [f for f in fixtures if str(f.get("id", "")) not in known_ids]

        if new_fix:
            logger.info(f"  → {len(new_fix)} novas para enviar (season {sid})")
            all_new.extend(new_fix)
            standings_by_s[sid] = fetch_standings(sid)
        else:
            seasons_vazias += 1

    logger.info(f"Resumo: {len(all_new)} partidas novas | {seasons_vazias} seasons sem novidade")

    ok = send_to_server(all_new, standings_by_s)

    if not ok and all_new:
        sys.exit(1)

    logger.info("✅ Concluído.")


if __name__ == "__main__":
    main()
