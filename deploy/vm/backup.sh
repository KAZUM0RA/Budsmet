#!/usr/bin/env bash
# Резервна копія бази кошторисів. Безпечна для роботи «на гарячу» —
# використовує механізм резервного копіювання самого SQLite.
#   sudo /opt/budsmet/app/deploy/vm/backup.sh [каталог]
set -euo pipefail

APP_NAME="budsmet"
DB="/var/lib/${APP_NAME}/budsmet.db"
DEST="${1:-/var/backups/${APP_NAME}}"
KEEP="${BUDSMET_BACKUP_KEEP:-14}"

[[ -f "$DB" ]] || { echo "Базу не знайдено: $DB"; exit 1; }
mkdir -p "$DEST"

STAMP="$(date +%Y%m%d-%H%M%S)"
FILE="${DEST}/budsmet-${STAMP}.db"

if command -v sqlite3 >/dev/null 2>&1; then
    sqlite3 "$DB" ".backup '${FILE}'"
else
    /opt/${APP_NAME}/venv/bin/python - "$DB" "$FILE" <<'PY'
import sqlite3
import sys

source, target = sys.argv[1], sys.argv[2]
with sqlite3.connect(source) as src, sqlite3.connect(target) as dst:
    src.backup(dst)
PY
fi

gzip -f "$FILE"
echo "Копію збережено: ${FILE}.gz ($(du -h "${FILE}.gz" | cut -f1))"

# Прибирання старих копій.
find "$DEST" -name 'budsmet-*.db.gz' -type f -printf '%T@ %p\n' \
    | sort -rn | tail -n "+$((KEEP + 1))" | cut -d' ' -f2- | xargs -r rm -f
