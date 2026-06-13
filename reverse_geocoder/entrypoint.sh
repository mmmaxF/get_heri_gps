#!/usr/bin/env bash
set -euo pipefail

DB_PATH="${GEOCODER_DB_PATH:-/app/data/admin_area.sqlite}"
AUTO_UPDATE="${GEOCODER_AUTO_UPDATE:-1}"

mkdir -p "$(dirname "$DB_PATH")" /app/output

if [ "$AUTO_UPDATE" != "0" ]; then
  python /app/import_admin_areas.py || {
    echo "行政区域DBの更新に失敗しました。既存DBがあればそれを使って起動します。" >&2
  }
fi

if [ ! -f "$DB_PATH" ]; then
  python /app/import_admin_areas.py --empty
fi

python /app/app.py
