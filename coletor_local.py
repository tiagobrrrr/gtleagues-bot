"""
coletor_local.py — GT Scout Bot
Roda no seu PC (IP residencial) a cada 15 minutos.
Coleta partidas finalizadas da GT Leagues API e envia ao servidor Render via webhook.
"""

import os
import time
import logging
import requests
from datetime import datetime, timedelta

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%d/%m/%Y %H:%M:%S"
)
logger = logging.getLogger(__name__)

# ── Configurações ─────────────────────────────────────────────
# Edite aqui ou crie um arquivo .env na mesma pasta
SERVER_URL   = os.getenv("SERVER_URL",   "https://gtleagues-bot.onrender.com")
WEBHOOK_KEY  = os.getenv("WEBHOOK_KEY",  "gtscout-webhook-2026")
GT_SEASON_IDS = os.getenv("GT_SEASON_IDS", "19211")   # separados por vírgula
INTERVAL_MIN = int(os.getenv("INTERVAL_MIN", "15"))

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
    "sec-ch-ua":          '"Chromium";v="124", "Google Chrome";v="124"',
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
        logger.warning(f"HTTP {r.status_code} → {url}")
    except Exception as e:
        logger.error(f"Erro GET {url}: {e}")
    return None


def get_known_ids():
    """Busca os match_ids já salvos no servidor para evitar reenvio."""
    try:
        r = requests.get(f"{SERVER_URL}/api/known-ids", timeout=15)
        if r.status_code == 200:
            return set(r.json().get("ids", []))
    except Exception as e:
        logger.error(f"Erro ao buscar known-ids: {e}")
    return set()


def fetch_fixtures_finalized(season_id):
    """Busca partidas finalizadas (status == 3) de uma season."""
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
    """Busca a classificação de uma season."""
    data = _get(f"/seasons/{season_id}/standings", {"limit": 500, "offset": 0})
    if not data:
        return []
    items = data if isinstance(data, list) else data.get("data", [])
    return items if isinstance(items, list) else []


def fetch_active_seasons():
    """Busca seasons ativas/recentes dos últimos 60 dias."""
    data = _get("/seasons", {"limit": 200, "offset": 0})
    if not data:
        return []
    items = data if isinstance(data, list) else data.get("data", [])
    if not isinstance(items, list):
        return []

    cutoff = datetime.now() - timedelta(days=60)
    active = []
    for s in items:
        sid    = str(s.get("id", ""))
        if not sid:
            continue
        status   = s.get("status")
        end_date = s.get("endDate") or s.get("end_date") or ""

        if status in (1, "1", "active"):
            active.append(sid)
            continue
        if end_date:
            try:
                ed = datetime.fromisoformat(end_date[:10])
                if ed >= cutoff:
                    active.append(sid)
            except Exception:
                active.append(sid)

    logger.info(f"Seasons ativas/recentes da API: {len(active)}")
    return active


def get_seasons():
    """Combina seasons do .env com seasons ativas da API."""
    env_ids = set(s.strip() for s in GT_SEASON_IDS.split(",") if s.strip())
    api_ids = set(fetch_active_seasons())
    combined = (api_ids | env_ids) if api_ids else env_ids
    logger.info(f"Total de seasons a varrer: {len(combined)}")
    return list(combined)


def send_to_server(fixtures, standings_by_season):
    """Envia partidas e standings ao servidor via webhook."""
    if not fixtures and not standings_by_season:
        return True

    # Flatten standings com season_id embutido
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
            json=payload,
            timeout=60
        )
        if r.status_code == 200:
            data = r.json()
            logger.info(
                f"  Servidor recebeu: +{data.get('new',0)} novas | "
                f"{data.get('updated',0)} atualizadas | "
                f"{data.get('players',0)} players"
            )
            return True
        else:
            logger.error(f"Webhook retornou {r.status_code}: {r.text[:200]}")
    except Exception as e:
        logger.error(f"Erro ao enviar para o servidor: {e}")
    return False


def run_cycle():
    """Executa um ciclo completo de coleta e envio."""
    now = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    logger.info("=" * 55)
    logger.info(f"[{now}] CICLO DE COLETA — coletor_local.py")
    logger.info("=" * 55)

    # 1. Busca IDs já no servidor (evita reenviar o que já existe)
    known_ids = get_known_ids()
    logger.info(f"IDs já no servidor: {len(known_ids)}")

    # 2. Determina seasons
    season_ids = get_seasons()
    if not season_ids:
        logger.error("Nenhuma season configurada!")
        return

    # 3. Coleta por season
    all_new_fixtures     = []
    standings_by_season  = {}

    for sid in season_ids:
        fixtures = fetch_fixtures_finalized(sid)
        new_fix  = [f for f in fixtures if str(f.get("id", "")) not in known_ids]

        if new_fix:
            all_new_fixtures.extend(new_fix)
            standings_by_season[sid] = fetch_standings(sid)
            logger.info(f"  Season {sid}: {len(new_fix)} novas para enviar")
        else:
            logger.info(f"  Season {sid}: nada novo")

    # 4. Envia ao servidor
    if all_new_fixtures:
        logger.info(f"Enviando {len(all_new_fixtures)} partidas ao servidor...")
        send_to_server(all_new_fixtures, standings_by_season)
    else:
        logger.info("Nenhuma partida nova para enviar.")

    logger.info(f"Próximo ciclo em {INTERVAL_MIN} minutos.\n")


if __name__ == "__main__":
    logger.info("╔══════════════════════════════════════════╗")
    logger.info("║     GT Scout — Coletor Local             ║")
    logger.info(f"║     Servidor: {SERVER_URL[:28]:<28}║")
    logger.info(f"║     Intervalo: {INTERVAL_MIN} minutos               ║")
    logger.info("╚══════════════════════════════════════════╝")

    # Carrega .env se existir
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())
        logger.info(".env carregado.")

    while True:
        try:
            run_cycle()
        except Exception as e:
            logger.error(f"Erro inesperado no ciclo: {e}")
        time.sleep(INTERVAL_MIN * 60)
