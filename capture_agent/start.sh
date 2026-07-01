#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${ENV_FILE:-${SCRIPT_DIR}/.env}"

if [[ ! -f "${ENV_FILE}" ]]; then
  cp "${SCRIPT_DIR}/.env.example" "${ENV_FILE}"
  echo "設定ファイルを作成しました: ${ENV_FILE}"
fi

if [[ "${1:-}" == "--headless" ]]; then
  shift
  exec python3 "${SCRIPT_DIR}/capture_agent.py" --env-file "${ENV_FILE}" "$@"
fi

CONTROL_SOCKET="$(sed -n 's/^CAPTURE_AGENT_CONTROL_SOCKET=//p' "${ENV_FILE}" | head -n 1 | tr -d "\"'")"
CONTROL_SOCKET="${CONTROL_SOCKET:-run/control.sock}"
if [[ "${CONTROL_SOCKET}" != /* ]]; then
  CONTROL_SOCKET="${SCRIPT_DIR}/${CONTROL_SOCKET}"
fi

if python3 - "${CONTROL_SOCKET}" >/dev/null 2>&1 <<'PY'
import socket
import sys

client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
client.settimeout(1)
client.connect(sys.argv[1])
client.sendall(b"GET /api/status HTTP/1.0\r\nHost: local\r\n\r\n")
response = client.recv(64)
client.close()
if b" 200 " not in response:
    raise SystemExit(1)
PY
then
  echo "capture-agent制御APIはすでに起動しています。"
  echo "操作はGPS受信UIから行います: http://127.0.0.1:8010/"
  exit 0
fi

exec python3 "${SCRIPT_DIR}/web_app.py" --env-file "${ENV_FILE}" "$@"
