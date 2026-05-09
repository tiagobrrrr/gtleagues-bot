#!/usr/bin/env bash
set -e
echo "==> Instalando dependências..."
pip install --upgrade pip
pip install greenlet>=3.0.3
pip install -r requirements.txt
echo "==> Instalando Chromium para Playwright..."
python -m playwright install chromium --with-deps
echo "==> Build concluído!"
