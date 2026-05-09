#!/usr/bin/env bash
set -e
echo "==> Python: $(python --version)"
echo "==> Instalando dependencias..."
pip install --upgrade pip
pip install -r requirements.txt

echo "==> Instalando Chromium (sem deps de sistema)..."
# --with-deps requer sudo — nao funciona no Render
# O Render ja tem as libs necessarias instaladas
PLAYWRIGHT_BROWSERS_PATH=/opt/render/project/.playwright \
    python -m playwright install chromium

echo "==> Build OK!"
