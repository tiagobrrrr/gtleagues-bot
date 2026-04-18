"""
coletor_local.py — GT Scout Bot
Roda no SEU PC (Windows) — IP residencial passa pelo Cloudflare.
Coleta TODAS as partidas históricas + continua coletando as novas.

Como usar:
  python coletor_local.py              → coleta normal (só novas)
  python coletor_local.py --full       → coleta completa histórica
  python coletor_local.py --descobre   → descobre todos os season IDs

Agendador de Tarefas Windows: a cada 15 minutos sem argumento.
"""

import requests
import logging
import sys
import time
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)

# ── CONFIGURAÇÕES ─────────────────────────────────────────────
RENDER_URL  = "https://gtleagues-bot.onrender.com"
WEBHOOK_KEY = "gtscout-webhook-2026"
API_BASE    = "https://api.gtleagues.com/api"

# Range de season IDs para busca histórica completa
# Ajuste SEASON_ID_MIN se souber que existem seasons mais antigas
SEASON_ID_MIN = 18000
SEASON_ID_MAX = 20000   # será atualizado automaticamente

# IDs conhecidos — usados se a descoberta automática falhar
SEASON_IDS_FALLBACK = [
    19415, 19414, 19413, 19412, 19411, 19410,
    19371, 19370, 19369, 19368, 19367,
    19211, 19210, 19209, 19208, 19207, 19206,
]
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


# ── Requisição ────────────────────────────────────────────────
def fetch(url, params=None, silent=False):
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=20)
        if r.status_code == 200:
            return r.json()
        if not silent:
            logger.warning(f"  HTTP {r.status_code} em {url}")
        return None
    except Exception as e:
        if not silent:
            logger.error(f"  Erro: {e}")
        return None


# ── Descobre todos os season IDs existentes ───────────────────
def descobrir_seasons():
    """
    Descobre todos os season IDs válidos tentando o endpoint de standings.
    Varre um range e coleta os que respondem 200.
    """
    logger.info("Descobrindo todos os season IDs (pode demorar alguns minutos)...")
    ids_validos = []

    # Primeiro tenta o endpoint de lista de seasons
    data = fetch(f"{API_BASE}/sports/6/seasons", {"limit": 500, "offset": 0}, silent=True)
    if data:
        items = data if isinstance(data, list) else data.get("data", data.get("seasons", []))
        if isinstance(items, list) and items:
            ids = [str(item.get("id")) for item in items if item.get("id")]
            logger.info(f"  Seasons via API: {len(ids)} encontrados")
            return sorted([int(i) for i in ids], reverse=True)

    # Fallback: varre range de IDs testando standings (mais rápido que fixtures)
    logger.info(f"  Varrendo range {SEASON_ID_MIN} → {SEASON_ID_MAX}...")
    batch_size = 50
    encontrados = 0

    for sid in range(SEASON_ID_MAX, SEASON_ID_MIN - 1, -1):
        data = fetch(f"{API_BASE}/seasons/{sid}/standings",
                     {"limit": 1, "offset": 0}, silent=True)
        if data:
            players = data.get("data", data) if isinstance(data, dict) else data
            if isinstance(players, list) and len(players) > 0:
                ids_validos.append(sid)
                encontrados += 1
                if encontrados % 10 == 0:
                    logger.info(f"  {encontrados} seasons encontrados até agora... (último: {sid})")
        time.sleep(0.1)  # não sobrecarregar a API

    logger.info(f"  Total: {len(ids_validos)} seasons válidos")
    return sorted(ids_validos, reverse=True)


# ── Coleta fixtures de uma season ─────────────────────────────
def coletar_season(season_id):
    fixtures  = []
    standings = []

    data = fetch(f"{API_BASE}/seasons/{season_id}/fixtures",
                 {"limit": 1000, "offset": 0})
    if data:
        items = data if isinstance(data, list) else data.get("data", data.get("fixtures", []))
        if isinstance(items, list):
            done = [f for f in items if isinstance(f, dict) and f.get("status") == 3]
            fixtures = done

    data2 = fetch(f"{API_BASE}/seasons/{season_id}/standings",
                  {"limit": 1000, "offset": 0})
    if data2:
        players = data2.get("data", data2) if isinstance(data2, dict) else data2
        if isinstance(players, list):
            standings = [{**p, "_season_id": season_id} for p in players]

    return fixtures, standings


# ── Acorda o Render ───────────────────────────────────────────
def wake_up_bot():
    try:
        logger.info("Acordando o bot no Render...")
        r = requests.get(f"{RENDER_URL}/", timeout=60)
        logger.info(f"  Bot respondeu: HTTP {r.status_code}")
        return r.status_code == 200
    except Exception as e:
        logger.warning(f"  Aviso: {e}")
        return False


# ── IDs já salvos no banco ────────────────────────────────────
def get_known_ids():
    try:
        r = requests.get(f"{RENDER_URL}/api/known-ids", timeout=30)
        if r.status_code == 200:
            ids = set(str(i) for i in r.json().get("ids", []))
            logger.info(f"Bot já tem {len(ids)} partidas no banco.")
            return ids
    except Exception as e:
        logger.warning(f"Não foi possível buscar IDs conhecidos: {e}")
    return set()


# ── Envia com retry ───────────────────────────────────────────
def post_with_retry(url, payload):
    for tentativa in range(1, 4):
        try:
            r = requests.post(url, json=payload, timeout=90)
            if r.status_code == 200:
                return r.json()
            elif r.status_code in (502, 503, 504):
                espera = tentativa * 20
                logger.warning(f"  HTTP {r.status_code}, aguardando {espera}s...")
                time.sleep(espera)
            else:
                logger.error(f"  HTTP {r.status_code}: {r.text[:200]}")
                return None
        except requests.exceptions.Timeout:
            logger.warning(f"  Timeout, aguardando {tentativa * 10}s...")
            time.sleep(tentativa * 10)
        except Exception as e:
            logger.error(f"  Erro: {e}")
            return None
    return None


# ── Envia em lotes ────────────────────────────────────────────
def send_to_bot(fixtures, standings):
    url   = f"{RENDER_URL}/webhook/ingest"
    BATCH = 40
    total_new = total_upd = total_play = 0

    all_items = list(zip(
        [fixtures[i:i+BATCH] for i in range(0, max(len(fixtures), 1), BATCH)],
        [standings[i:i+BATCH] for i in range(0, max(len(standings), 1), BATCH)]
        + [[]] * 999
    ))

    # Garante pelo menos 1 lote
    fix_lotes = [fixtures[i:i+BATCH] for i in range(0, max(len(fixtures), 1), BATCH)]
    std_lotes = [standings[i:i+BATCH] for i in range(0, max(len(standings), 1), BATCH)]

    total_lotes = max(len(fix_lotes), len(std_lotes))

    for i in range(total_lotes):
        lf = fix_lotes[i] if i < len(fix_lotes) else []
        ls = std_lotes[i] if i < len(std_lotes) else []
        logger.info(f"  Lote {i+1}/{total_lotes}: {len(lf)} partidas, {len(ls)} players")

        result = post_with_retry(url, {"key": WEBHOOK_KEY, "fixtures": lf, "standings": ls})
        if result:
            total_new  += result.get("new", 0)
            total_upd  += result.get("updated", 0)
            total_play += result.get("players", 0)
        else:
            logger.error(f"  ❌ Lote {i+1} falhou")

        if i < total_lotes - 1:
            time.sleep(2)

    logger.info(f"  ✅ {total_new} novas | {total_upd} já existiam | {total_play} players")
    return total_new


# ── MAIN ──────────────────────────────────────────────────────
if __name__ == "__main__":
    modo_full     = "--full"     in sys.argv
    modo_descobre = "--descobre" in sys.argv

    agora = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    logger.info("="*55)
    logger.info(f"GT Scout — Coletor Local | {agora}")
    modo_str = "COMPLETO HISTÓRICO" if modo_full else ("DESCOBRE SEASONS" if modo_descobre else "INCREMENTAL")
    logger.info(f"Modo: {modo_str}")
    logger.info("="*55)

    # ── Descobre seasons ──────────────────────────────────────
    if modo_descobre:
        ids = descobrir_seasons()
        logger.info(f"\nSeason IDs encontrados:\n{ids}")
        logger.info(f"\nColoca no Render → GT_SEASON_IDS:\n{','.join(str(i) for i in ids)}")
        sys.exit(0)

    # ── Define quais seasons coletar ──────────────────────────
    if modo_full:
        season_ids = descobrir_seasons()
        if not season_ids:
            season_ids = SEASON_IDS_FALLBACK
    else:
        season_ids = SEASON_IDS_FALLBACK

    logger.info(f"Coletando {len(season_ids)} seasons...")

    # ── Coleta da API ─────────────────────────────────────────
    all_fixtures  = []
    all_standings = []

    for season_id in season_ids:
        fixtures, standings = coletar_season(season_id)
        if fixtures:
            logger.info(f"  Season {season_id}: {len(fixtures)} partidas | {len(standings)} players")
        all_fixtures.extend(fixtures)
        all_standings.extend(standings)

    agora2 = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    logger.info(f"\n[{agora2}] Total coletado: {len(all_fixtures)} partidas | {len(all_standings)} registros")

    if not all_fixtures and not all_standings:
        logger.warning("Nada coletado. Verifique sua conexão.")
        sys.exit(1)

    # ── Acorda o bot ──────────────────────────────────────────
    wake_up_bot()

    # ── Filtra novas (modo incremental) ───────────────────────
    if not modo_full:
        known = get_known_ids()
        if known:
            antes = len(all_fixtures)
            all_fixtures = [f for f in all_fixtures if str(f.get("id", "")) not in known]
            pulados = antes - len(all_fixtures)
            if pulados:
                logger.info(f"Ignorando {pulados} já salvas. Enviando {len(all_fixtures)} novas.")

    if not all_fixtures and not all_standings:
        agora3 = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        logger.info(f"[{agora3}] ✅ Nenhuma partida nova. Bot atualizado.")
        sys.exit(0)

    # ── Envia para o Render ───────────────────────────────────
    logger.info(f"\nEnviando {len(all_fixtures)} partidas em lotes...")
    novas = send_to_bot(all_fixtures, all_standings)

    agora4 = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    logger.info(f"\n[{agora4}] ✅ CONCLUÍDO — {novas} novas partidas salvas no bot.")
