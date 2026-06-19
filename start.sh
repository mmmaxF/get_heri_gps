#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if [ ! -f .env ]; then
  cp .env.example .env
  echo ".env がなかったため .env.example から作成しました。"
fi

for service_dir in gps_receiver reverse_geocoder; do
  if [ ! -f "${service_dir}/.env" ]; then
    cp "${service_dir}/.env.example" "${service_dir}/.env"
    echo "${service_dir}/.env がなかったため ${service_dir}/.env.example から作成しました。"
  fi
done

env_value() {
  local key="$1"
  local default="$2"
  local line
  line="$(grep -E "^${key}=" .env | tail -n 1 || true)"
  if [ -z "${line}" ]; then
    echo "${default}"
  else
    echo "${line#*=}"
  fi
}

APP_PORT="$(env_value APP_PORT 8010)"
APP_PORT="$(env_value GPS_RECEIVER_PORT "${APP_PORT}")"
APP_PUBLIC_HOST="$(env_value APP_PUBLIC_HOST 127.0.0.1)"
REVERSE_GEOCODER_PORT="$(env_value REVERSE_GEOCODER_PORT 8020)"
APP_URL="http://${APP_PUBLIC_HOST}:${APP_PORT}"
LAN_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"

if ! command -v docker >/dev/null 2>&1; then
  echo "Dockerが見つかりません。Dockerをインストールしてください。" >&2
  exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
  echo "docker compose が見つかりません。Docker Composeを有効にしてください。" >&2
  exit 1
fi

mkdir -p "$(env_value HOST_GPS_OUTPUT_DIR ./gps_receiver/output)"
mkdir -p "$(env_value HOST_GPS_INPUT_DIR ./gps_receiver/input)"
mkdir -p "$(env_value HOST_GPS_LOG_DIR ./gps_receiver/logs)"
mkdir -p "$(env_value HOST_GEOCODER_DATA_DIR ./reverse_geocoder/data)"
mkdir -p "$(env_value HOST_GEOCODER_OUTPUT_DIR ./reverse_geocoder/output)"
mkdir -p "$(env_value HOST_GEOCODER_LOG_DIR ./reverse_geocoder/logs)"

echo "get_heri_gps Dockerコンテナを作成・起動します..."
docker compose up -d --build

echo
docker compose ps

echo
echo "起動しました。"
echo "ローカル: ${APP_URL}"
if [ -n "${LAN_IP}" ]; then
echo "LAN:      http://${LAN_IP}:${APP_PORT}"
fi
echo "逆ジオコーダーAPI: http://127.0.0.1:${REVERSE_GEOCODER_PORT}/api/health"
echo
echo "ログ確認: docker compose logs -f"
echo "停止:     docker compose down"
