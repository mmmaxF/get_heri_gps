#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

APP_URL="http://127.0.0.1:8010"
LAN_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"

if ! command -v docker >/dev/null 2>&1; then
  echo "Dockerが見つかりません。Dockerをインストールしてください。" >&2
  exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
  echo "docker compose が見つかりません。Docker Composeを有効にしてください。" >&2
  exit 1
fi

mkdir -p output

echo "get_heri_gps Dockerコンテナを作成・起動します..."
docker compose up -d --build

echo
docker compose ps

echo
echo "起動しました。"
echo "ローカル: ${APP_URL}"
if [ -n "${LAN_IP}" ]; then
echo "LAN:      http://${LAN_IP}:8010"
fi
echo "逆ジオコーダーAPI: http://127.0.0.1:8020/api/health"
echo "テロップAPI:       http://127.0.0.1:8030/api/status"
echo
echo "ログ確認: docker compose logs -f"
echo "停止:     docker compose down"
