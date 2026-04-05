# 🏆 GT Scout Bot

Bot de coleta e análise de resultados da **GT Leagues**, com dashboard completo e métricas de média de gols.

## 📁 Estrutura

```
gtleagues-bot/
├── app.py              # Flask + scheduler
├── web_scraper.py      # Coleta da API GT Leagues
├── models.py           # Modelos SQLAlchemy (SQLite)
├── requirements.txt
├── .env.example
└── templates/
    ├── layout.html
    ├── index.html       # Dashboard
    ├── matches.html     # Partidas
    ├── statistics.html  # Estatísticas + Médias de Gols
    ├── charts.html      # Gráficos
    ├── head_to_head.html # Confrontos H2H
    ├── players.html     # Classificação
    └── reports.html     # Relatórios
```

## 🚀 Instalação

```bash
pip install -r requirements.txt
cp .env.example .env
# Edite o .env com a URL da API e IDs de temporada
python app.py
```

## ⚙️ Configuração (.env)

| Variável | Descrição | Padrão |
|---|---|---|
| `GT_API_BASE_URL` | URL base da API GT Leagues | `https://www.gtleagues.com/api` |
| `GT_SEASON_IDS` | IDs das temporadas (vírgula) | `19211` |
| `SCRAPER_INTERVAL_MINUTES` | Intervalo de varredura | `5` |
| `FLASK_PORT` | Porta do servidor | `5000` |

## 📊 Métricas de Média de Gols

### Individual
- **Média GF/Partida**: total de gols marcados ÷ partidas jogadas
- **Média GC/Partida**: total de gols sofridos ÷ partidas jogadas
- **Média Total/Partida**: soma de todos os gols da partida ÷ jogos

### H2H (Confronto Direto)
- Mesmas métricas calculadas **somente nos confrontos entre os dois players selecionados**
- Histórico completo de todos os jogos entre eles

## 🔄 Lógica de Coleta

- Varredura automática a cada **5 minutos**
- Coleta apenas partidas com `status = 3` (finalizadas)
- Upsert: atualiza se já existir, insere se for nova
- Banco SQLite local (`gtscout.db`)
- Dashboard atualiza automaticamente a cada 5 min no browser
