#!/usr/bin/env bash
# Оновлення Budsmet до останньої версії з репозиторію.
#   sudo /opt/budsmet/app/deploy/vm/update.sh
set -euo pipefail

APP_NAME="budsmet"
APP_DIR="/opt/${APP_NAME}/app"
VENV_DIR="/opt/${APP_NAME}/venv"
DATA_DIR="/var/lib/${APP_NAME}"

[[ $EUID -eq 0 ]] || { echo "Запустіть через sudo"; exit 1; }

git config --global --add safe.directory "$APP_DIR" 2>/dev/null || true
BRANCH="$(git -C "$APP_DIR" rev-parse --abbrev-ref HEAD)"

echo "==> Резервна копія бази перед оновленням"
"${APP_DIR}/deploy/vm/backup.sh" >/dev/null

echo "==> Отримання оновлень (гілка ${BRANCH})"
git -C "$APP_DIR" fetch --depth 1 origin "$BRANCH"
git -C "$APP_DIR" reset --hard "origin/${BRANCH}"
chown -R "$APP_NAME:$APP_NAME" "$APP_DIR"

echo "==> Оновлення залежностей"
"${VENV_DIR}/bin/pip" install --quiet --upgrade -r "${APP_DIR}/requirements.txt"
chown -R "$APP_NAME:$APP_NAME" "$VENV_DIR"

echo "==> Перезапуск служби"
systemctl restart "$APP_NAME"
sleep 2

if curl -fsS --max-time 10 http://127.0.0.1:8000/api/health >/dev/null; then
    echo "✓ Оновлено. Версія: $(git -C "$APP_DIR" rev-parse --short HEAD)"
    echo "  База даних не змінювалась: ${DATA_DIR}/budsmet.db"
else
    echo "✗ Застосунок не відповідає після оновлення:"
    journalctl -u "$APP_NAME" -n 30 --no-pager
    exit 1
fi
