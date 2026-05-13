# 🏆 GT Scout Bot

Bot completo para coleta e análise de partidas da **GT Leagues** (gtleagues.com).

## ✨ Funcionalidades

- ✅ Coleta automática via **GitHub Actions** a cada 15 minutos (bypassa Cloudflare)
- ✅ Dashboard com partidas recentes, agendadas e estatísticas
- ✅ Estatísticas individuais: GF, GC, saldo, médias por partida
- ✅ Confronto direto (Head-to-Head) entre players
- ✅ Gráficos com busca por player
- ✅ Exportação Excel por contexto (partidas, estatísticas, H2H, gráficos)
- ✅ Email semanal automático com planilha de partidas
- ✅ Deploy no Render (Flask + PostgreSQL)

## 📁 Estrutura

```
gtleagues-bot/
├── app.py                    # Flask: rotas e scheduler
├── data_analyzer.py          # Estatísticas e H2H
├── statistics_calculator.py  # Cálculos avançados (ranking, goleadas)
├── report_generator.py       # Geração de relatórios e planilhas
├── email_service.py          # Envio de email
├── excel_exporter.py         # Exportação Excel
├── web_scraper.py            # Parser e upsert (webhook)
├── models.py                 # Match, Player, PlayerStats
├── gh_coletor.py             # Coleta via GitHub Actions
└── .github/workflows/
    └── coletor.yml           # Cron 15 min automático
```

## 🚀 Deploy

### 1. Render (servidor)
- Build Command: `./build.sh`
- Start Command: `gunicorn app:app`

Variáveis de ambiente no Render:
```
DATABASE_URL=postgresql://...
GT_SEASON_IDS=19211
WEBHOOK_KEY=gtscout-webhook-2026
SECRET_KEY=seu-secret-key
```

### 2. GitHub Actions (coleta)
Adicione em **Settings → Secrets → Actions**:
```
SERVER_URL=https://gtleagues-bot.onrender.com
WEBHOOK_KEY=gtscout-webhook-2026
GT_SEASON_IDS=19211
```

## 🔗 Rotas

| Rota | Descrição |
|---|---|
| `/` | Dashboard |
| `/matches` | Partidas coletadas |
| `/scheduled` | Partidas agendadas |
| `/statistics` | Estatísticas individuais |
| `/players` | Lista de players |
| `/head-to-head` | Confronto direto |
| `/charts` | Gráficos |
| `/reports` | Relatórios |
| `/download/excel/reports` | Planilha de partidas |
| `/download/excel/stats` | Planilha de estatísticas |
| `/download/excel/charts` | Planilha de gráficos |
| `/download/excel/h2h` | Planilha de confrontos |
| `/api/status` | Status JSON |
| `/api/known-ids` | IDs salvos (usado pelo coletor) |
| `/webhook/ingest` | Recebe dados do GitHub Actions |

## 📊 Arquitetura de Coleta

```
GitHub Actions (IPs não bloqueados pelo Cloudflare)
  → gh_coletor.py roda a cada 15 min
  → Busca partidas em api.gtleagues.com
  → Envia via POST /webhook/ingest
  → Render salva no PostgreSQL
  → Dashboard exibe os dados
```

## 📧 Email Semanal

Configure as variáveis:
```
EMAIL_USER=seu@gmail.com
EMAIL_PASSWORD=sua-app-password
EMAIL_RECIPIENT=destinatario@email.com
EMAIL_WEEKLY_DAY=0   # 0=segunda, 6=domingo
EMAIL_WEEKLY_HOUR=8  # hora do envio
```
