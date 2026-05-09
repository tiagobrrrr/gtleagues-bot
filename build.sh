#!/usr/bin/env bash
set -e
echo "==> Python version: $(python --version)"
echo "==> Instalando dependencias..."
pip install --upgrade pip
pip install -r requirements.txt
echo "==> Instalando Chromium..."
python -m playwright install chromium --with-deps
echo "==> Build OK!"
