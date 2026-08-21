#!/usr/bin/env bash
#
# Перевіряє, чи з'явилась нова версія у гілці, і за потреби запускає оновлення.
# Викликається таймером systemd; вручну потрібен рідко.
#
set -euo pipefail

APP_NAME="budsmet"
APP_DIR="/opt/${APP_NAME}/app"
FAILED_MARK="/var/lib/${APP_NAME}/.failed-commit"

[[ $EUID -eq 0 ]] || { echo "Запустіть через sudo"; exit 1; }
[[ -d "${APP_DIR}/.git" ]] || { echo "Застосунок не встановлено в ${APP_DIR}"; exit 1; }

git config --global --add safe.directory "$APP_DIR" 2>/dev/null || true

BRANCH="$(git -C "$APP_DIR" rev-parse --abbrev-ref HEAD)"
LOCAL="$(git -C "$APP_DIR" rev-parse HEAD)"
REMOTE="$(git -C "$APP_DIR" ls-remote origin "$BRANCH" 2>/dev/null | awk '{print $1}')"

if [[ -z "$REMOTE" ]]; then
    echo "Не вдалося опитати репозиторій — пропускаю цю перевірку."
    exit 0
fi

if [[ "$LOCAL" == "$REMOTE" ]]; then
    echo "Версія актуальна (${LOCAL:0:7}), оновлювати нічого."
    exit 0
fi

# Версію, що вже валила застосунок, повторно не встановлюємо: інакше
# кожна перевірка означала б два зайві перезапуски служби.
if [[ -f "$FAILED_MARK" && "$(cat "$FAILED_MARK")" == "$REMOTE" ]]; then
    echo "Версія ${REMOTE:0:7} раніше не запустилась — пропускаю. Чекаю на наступний коміт."
    exit 0
fi

echo "Знайдено нову версію: ${LOCAL:0:7} → ${REMOTE:0:7}. Оновлюю."
exec "${APP_DIR}/deploy/vm/update.sh"
