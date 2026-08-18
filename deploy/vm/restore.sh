#!/usr/bin/env bash
# Відновлення бази кошторисів із резервної копії.
#   sudo /opt/budsmet/app/deploy/vm/restore.sh /var/backups/budsmet/budsmet-20260818-033000.db.gz
#
# Увага: SQLite працює в режимі WAL, тому просто підкласти файл бази недостатньо —
# поруч треба прибрати старі -wal і -shm, інакше застосунок побачить суміш даних.
set -euo pipefail

APP_NAME="budsmet"
DATA_DIR="/var/lib/${APP_NAME}"
DB="${DATA_DIR}/budsmet.db"
SRC="${1:-}"

[[ $EUID -eq 0 ]] || { echo "Запустіть через sudo"; exit 1; }
[[ -n "$SRC" && -f "$SRC" ]] || {
    echo "Вкажіть файл копії. Доступні:"
    ls -1t /var/backups/${APP_NAME}/*.gz 2>/dev/null | head -20 || echo "  (копій не знайдено)"
    exit 1
}

echo "Поточну базу буде замінено вмістом ${SRC}."
read -rp "Продовжити? Введіть «так»: " answer
[[ "$answer" == "так" ]] || { echo "Скасовано."; exit 0; }

echo "==> Зупинка застосунку"
systemctl stop "$APP_NAME"

if [[ -f "$DB" ]]; then
    SAFETY="${DB}.перед-відновленням-$(date +%Y%m%d-%H%M%S)"
    echo "==> Поточна база збережена як ${SAFETY}"
    # Переносимо разом із журналом: у режимі WAL самий лише .db порожній,
    # а без -wal страхувальна копія була б непридатною.
    mv "$DB" "$SAFETY"
    [[ -f "${DB}-wal" ]] && mv "${DB}-wal" "${SAFETY}-wal"
    [[ -f "${DB}-shm" ]] && mv "${DB}-shm" "${SAFETY}-shm"
fi
# Гарантуємо, що поруч із відновленою базою не лишилось чужого журналу.
rm -f "${DB}-wal" "${DB}-shm"

echo "==> Розпакування копії"
if [[ "$SRC" == *.gz ]]; then gunzip -c "$SRC" > "$DB"; else cp "$SRC" "$DB"; fi
chown "$APP_NAME:$APP_NAME" "$DB"
chmod 640 "$DB"

echo "==> Перевірка цілісності"
/opt/${APP_NAME}/venv/bin/python - "$DB" <<'PY'
import sqlite3
import sys

conn = sqlite3.connect(sys.argv[1])
status = conn.execute("PRAGMA integrity_check").fetchone()[0]
objects = conn.execute("SELECT COUNT(*) FROM objects").fetchone()[0]
positions = conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0]
print(f"    цілісність: {status}; об'єктів: {objects}; позицій: {positions}")
sys.exit(0 if status == "ok" else 1)
PY

echo "==> Запуск застосунку"
systemctl start "$APP_NAME"
sleep 2
curl -fsS --max-time 10 http://127.0.0.1:8000/api/health >/dev/null \
    && echo "✓ Базу відновлено, застосунок працює." \
    || { echo "✗ Застосунок не відповідає:"; journalctl -u "$APP_NAME" -n 30 --no-pager; exit 1; }
