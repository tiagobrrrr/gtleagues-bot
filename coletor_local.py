"""
coletor_local.py — Roda no SEU PC (Windows/Mac/Linux)
Busca os dados da API GT Leagues (IP residencial = sem bloqueio)
e envia para o bot no Render via webhook.

Como usar:
  1. pip install requests
  2. Edite RENDER_URL e GT_SEASON_IDS abaixo
  3. python coletor_local.py
  4. Configure no Agendador de Tarefas do Windows para rodar a cada 15 min
"""

import requests
import logging
import sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger(__name__)

# ── CONFIGURAÇÕES — EDITE AQUI ────────────────────────────────
RENDER_URL   = "https://gtleagues-bot.onrender.com"  # URL do seu bot no Render
WEBHOOK_KEY  = "gtscout-webhook-2026"               # Chave secreta (deve ser igual no .env)
GT_SEASON_IDS = [
    19415, 19414, 19413, 19412, 19411, 19410,
    19371, 19370, 19369, 19368, 19367, 19211
]
API_BASE = "https://api.gtleagues.com/api"
# ─────────────────────────────────────────────────────────────

HEADERS = {
    "accept": "application/json",
    "accept-language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    "origin": "https://www.gtleagues.com",
    "referer": "https://www.gtleagues.com/",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
    "sec-ch-ua": '"Chromium";v="146", "Not-A.Brand";v="24", "Google Chrome";v="146"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-site",
}


def fetch(url, params=None):
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=20)
        if r.status_code == 200:
            return r.json()
        logger.warning(f"HTTP {r.status_code} em {url}")
        return None
    except Exception as e:
        logger.error(f"Erro em {url}: {e}")
        return None


def collect():
    all_fixtures  = []
    all_standings = []

    for season_id in GT_SEASON_IDS:
        logger.info(f"Buscando season {season_id}...")

        # Fixtures finalizadas
        data = fetch(f"{API_BASE}/seasons/{season_id}/fixtures", {"limit": 1000, "offset": 0})
        if data:
            items = data if isinstance(data, list) else data.get("data", data.get("fixtures", []))
            done  = [f for f in items if isinstance(f, dict) and f.get("status") == 3]
            logger.info(f"  {len(done)} partidas finalizadas")
            all_fixtures.extend(done)

        # Standings
        data2 = fetch(f"{API_BASE}/seasons/{season_id}/standings", {"limit": 1000, "offset": 0})
        if data2:
            players = data2.get("data", data2) if isinstance(data2, dict) else data2
            if isinstance(players, list):
                logger.info(f"  {len(players)} players")
                all_standings.extend([{**p, "_season_id": season_id} for p in players])

    return all_fixtures, all_standings


def send_to_bot(fixtures, standings):
    url = f"{RENDER_URL}/webhook/ingest"
    payload = {
        "key":       WEBHOOK_KEY,
        "fixtures":  fixtures,
        "standings": standings,
    }
    try:
        r = requests.post(url, json=payload, timeout=60)
        if r.status_code == 200:
            result = r.json()
            logger.info(f"✅ Bot recebeu: {result}")
        else:
            logger.error(f"❌ Bot retornou HTTP {r.status_code}: {r.text[:200]}")
    except Exception as e:
        logger.error(f"❌ Erro ao enviar para o bot: {e}")


if __name__ == "__main__":
    logger.info("="*50)
    logger.info("GT Scout — Coletor Local")
    logger.info("="*50)

    fixtures, standings = collect()
    logger.info(f"\nTotal coletado: {len(fixtures)} partidas | {len(standings)} registros de players")

    if fixtures or standings:
        logger.info("Enviando para o Render...")
        send_to_bot(fixtures, standings)
    else:
        logger.warning("Nada coletado — verifique sua conexão")
