#!/usr/bin/env bash
# Render roda este script no build
set -e
pip install -r requirements.txt
python -m playwright install chromium
python -m playwright install-deps chromium
