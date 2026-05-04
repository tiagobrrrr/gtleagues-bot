"""
coletor_local.py — GT Scout Bot
Roda no SEU PC — IP residencial passa pelo Cloudflare.

Modos:
  python coletor_local.py              → só partidas novas (uso diário)
  python coletor_local.py --full       → histórico completo
  python coletor_local.py --descobre   → descobre e salva IDs em seasons_descobertos.txt
"""

import requests, logging, sys, time, os
from datetime import datetime

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger(__name__)

RENDER_URL   = "https://gtleagues-bot.onrender.com"
WEBHOOK_KEY  = "gtscout-webhook-2026"
API_BASE     = "https://api.gtleagues.com/api"
SEASONS_FILE = "seasons_descobertos.txt"
BATCH_SIZE   = 30

HEADERS = {
    "accept": "application/json",
    "accept-language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    "origin": "https://www.gtleagues.com",
    "referer": "https://www.gtleagues.com/",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
    "sec-ch-ua": '"Chromium";v="146", "Not-A.Brand";v="24", "Google Chrome";v="146"',
    "sec-ch-ua-mobile": "?0", "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "empty", "sec-fetch-mode": "cors", "sec-fetch-site": "same-site",
}

def fetch(url, params=None, silent=False):
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=20)
        if r.status_code == 200: return r.json()
        if not silent: logger.warning(f"  HTTP {r.status_code}: {url}")
        return None
    except Exception as e:
        if not silent: logger.error(f"  Erro: {e}")
        return None

def salvar_seasons(ids):
    with open(SEASONS_FILE, "w") as f:
        f.write("\n".join(str(i) for i in sorted(ids, reverse=True)))
    logger.info(f"  {len(ids)} IDs salvos em '{SEASONS_FILE}'")

def carregar_seasons():
    if not os.path.exists(SEASONS_FILE): return None
    with open(SEASONS_FILE) as f:
        ids = [int(l.strip()) for l in f if l.strip().isdigit()]
    logger.info(f"  {len(ids)} season IDs carregados de '{SEASONS_FILE}'")
    return ids

def descobrir_seasons(id_min=18000, id_max=19600):
    logger.info(f"Descobrindo seasons {id_min}→{id_max} (aguarde)...")
    validos = []
    for i, sid in enumerate(range(id_max, id_min - 1, -1)):
        data = fetch(f"{API_BASE}/seasons/{sid}/standings", {"limit":1,"offset":0}, silent=True)
        if data:
            pl = data.get("data", data) if isinstance(data, dict) else data
            if isinstance(pl, list) and pl:
                validos.append(sid)
                if len(validos) % 50 == 0:
                    pct = round(((id_max - sid) / (id_max - id_min)) * 100)
                    logger.info(f"  {len(validos)} encontrados | {pct}% | último: {sid}")
        time.sleep(0.05)
    logger.info(f"  Total: {len(validos)} seasons")
    return sorted(validos, reverse=True)

def coletar_season(sid):
    fx, st = [], []
    d = fetch(f"{API_BASE}/seasons/{sid}/fixtures", {"limit":1000,"offset":0})
    if d:
        items = d if isinstance(d, list) else d.get("data", [])
        if isinstance(items, list):
            fx = [f for f in items if isinstance(f, dict) and f.get("status") == 3]
    d2 = fetch(f"{API_BASE}/seasons/{sid}/standings", {"limit":1000,"offset":0})
    if d2:
        pl = d2.get("data", d2) if isinstance(d2, dict) else d2
        if isinstance(pl, list):
            st = [{**p, "_season_id": sid} for p in pl]
    return fx, st

def wake_up():
    try:
        logger.info("Acordando o Render...")
        r = requests.get(f"{RENDER_URL}/", timeout=60)
        logger.info(f"  HTTP {r.status_code}")
        if r.status_code == 200: time.sleep(3); return True
    except Exception as e:
        logger.warning(f"  {e}")
    return False

def get_known_ids():
    try:
        r = requests.get(f"{RENDER_URL}/api/known-ids", timeout=30)
        if r.status_code == 200:
            ids = set(str(i) for i in r.json().get("ids", []))
            logger.info(f"  Banco já tem {len(ids)} partidas.")
            return ids
    except Exception as e:
        logger.warning(f"  {e}")
    return set()

def post_lote(fixtures, standings):
    url = f"{RENDER_URL}/webhook/ingest"
    payload = {"key": WEBHOOK_KEY, "fixtures": fixtures, "standings": standings}
    for t in range(1, 4):
        try:
            r = requests.post(url, json=payload, timeout=90)
            if r.status_code == 200: return r.json()
            elif r.status_code in (502, 503, 504):
                logger.warning(f"  HTTP {r.status_code}, aguardando {t*20}s...")
                time.sleep(t * 20)
            else:
                logger.error(f"  HTTP {r.status_code}: {r.text[:100]}")
                return None
        except Exception as e:
            logger.warning(f"  Tentativa {t}: {e}")
            time.sleep(t * 10)
    return None

def enviar(fixtures, standings):
    total_new = total_upd = total_play = 0
    fix_lotes = [fixtures[i:i+BATCH_SIZE] for i in range(0, max(len(fixtures),1), BATCH_SIZE)]
    std_lotes = [standings[i:i+BATCH_SIZE] for i in range(0, max(len(standings),1), BATCH_SIZE)]
    n = max(len(fix_lotes), len(std_lotes))
    for i in range(n):
        lf = fix_lotes[i] if i < len(fix_lotes) else []
        ls = std_lotes[i] if i < len(std_lotes) else []
        logger.info(f"  Lote {i+1}/{n}: {len(lf)} partidas")
        res = post_lote(lf, ls)
        if res:
            total_new  += res.get("new", 0)
            total_upd  += res.get("updated", 0)
            total_play += res.get("players", 0)
            logger.info(f"    ✅ novas={res.get('new',0)} | total acum.={total_new}")
        else:
            logger.error(f"    ❌ Lote {i+1} falhou")
        if i < n - 1: time.sleep(1)
    return total_new, total_upd, total_play

if __name__ == "__main__":
    modo_full     = "--full"     in sys.argv
    modo_descobre = "--descobre" in sys.argv

    ts = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    logger.info("=" * 55)
    logger.info(f"GT Scout — Coletor Local | {ts}")
    modo_str = "HISTÓRICO COMPLETO" if modo_full else ("DESCOBRIR SEASONS" if modo_descobre else "INCREMENTAL")
    logger.info(f"Modo: {modo_str}")
    logger.info("=" * 55)

    if modo_descobre:
        ids = descobrir_seasons()
        salvar_seasons(ids)
        logger.info(f"\nAgora rode: python coletor_local.py --full")
        sys.exit(0)

    # Carrega seasons
    season_ids = carregar_seasons()
    if not season_ids:
        if modo_full:
            season_ids = descobrir_seasons()
            salvar_seasons(season_ids)
        else:
            # Incremental sem arquivo: pega as últimas 400 seasons
            season_ids = list(range(19600, 19199, -1))
            logger.info(f"  Usando range recente: {len(season_ids)} seasons")

    logger.info(f"Coletando {len(season_ids)} seasons...")

    all_fx, all_st = [], []
    com_dados = 0
    for idx, sid in enumerate(season_ids):
        fx, st = coletar_season(sid)
        if fx:
            com_dados += 1
            logger.info(f"  [{idx+1}/{len(season_ids)}] Season {sid}: {len(fx)} partidas | {len(st)} players")
        all_fx.extend(fx)
        all_st.extend(st)

    ts2 = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    logger.info(f"\n[{ts2}] Coletado: {len(all_fx)} partidas | {com_dados} seasons")

    if not all_fx and not all_st:
        logger.warning("Nada coletado.")
        sys.exit(1)

    wake_up()

    if not modo_full:
        known = get_known_ids()
        if known:
            antes = len(all_fx)
            all_fx = [f for f in all_fx if str(f.get("id","")) not in known]
            pulados = antes - len(all_fx)
            if pulados:
                logger.info(f"  Ignorando {pulados} já salvas. Novas: {len(all_fx)}")

    if not all_fx and not all_st:
        logger.info(f"[{datetime.now().strftime('%d/%m/%Y %H:%M:%S')}] ✅ Nenhuma partida nova.")
        sys.exit(0)

    logger.info(f"\nEnviando {len(all_fx)} partidas em lotes de {BATCH_SIZE}...")
    novas, atualizadas, players = enviar(all_fx, all_st)

    ts4 = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    logger.info(f"\n[{ts4}] ✅ CONCLUÍDO: {novas} novas | {atualizadas} atualizadas | {players} players")
