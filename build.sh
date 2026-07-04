#!/usr/bin/env bash
set -e
echo "==> Python: $(python --version)"
pip install --upgrade pip
pip install -r requirements.txt

echo "==> Baixando certificado CockroachDB..."
mkdir -p /opt/render/.postgresql
curl -o /opt/render/.postgresql/root.crt \
  https://cockroachlabs.cloud/clusters/certs/cockroachdb-ca-cert.pem 2>/dev/null \
  || wget -O /opt/render/.postgresql/root.crt \
  https://cockroachlabs.cloud/clusters/certs/cockroachdb-ca-cert.pem 2>/dev/null \
  || echo "Aviso: nao foi possivel baixar certificado, usando sslmode=require"

echo "==> Build OK!"
