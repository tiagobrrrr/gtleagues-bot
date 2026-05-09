#!/usr/bin/env bash
set -e
echo "==> Python: $(python --version)"
echo "==> Instalando dependencias..."
pip install --upgrade pip
pip install -r requirements.txt

echo "==> Instalando Chromium no caminho padrao do Playwright..."
# Usa o caminho padrao: ~/.cache/ms-playwright
# No Render isso e /opt/render/.cache/ms-playwright (gravavel)
python -m playwright install chromium

echo "==> Verificando instalacao..."
python -c "from playwright.sync_api import sync_playwright; print('Playwright OK')"

echo "==> Build OK!"
