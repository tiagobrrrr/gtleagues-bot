#!/usr/bin/env bash
set -e
echo "==> Python: $(python --version)"
pip install --upgrade pip
pip install -r requirements.txt

# Instala Chromium dentro da pasta do projeto (persiste no Render)
export PLAYWRIGHT_BROWSERS_PATH=/opt/render/project/src/.browsers
echo "==> Instalando Chromium em $PLAYWRIGHT_BROWSERS_PATH ..."
python -m playwright install chromium
echo "==> Conteudo de .browsers:"
ls -la $PLAYWRIGHT_BROWSERS_PATH || true
echo "==> Build OK!"
