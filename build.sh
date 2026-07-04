#!/usr/bin/env bash
set -e
echo "==> Python: $(python --version)"
pip install --upgrade pip
pip install -r requirements.txt
echo "==> Build OK!"
