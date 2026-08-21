#!/usr/bin/env bash
# Оновлення Budsmet до останньої версії з репозиторію.
#   sudo /opt/budsmet/app/deploy/vm/update.sh
set -euo pipefail

# Скрипт лежить у репозиторії, який сам і оновлює: git підмінив би цей файл
# просто під час виконання, а bash дочитує його з диска по ходу. Тому одразу
# продовжуємо роботу з копії у тимчасовому каталозі.
if [[ "${BUDSMET_REEXEC:-}" != "1" ]]; then
    _self_copy="$(mktemp /tmp/budsmet-XXXXXX.sh)"
    cp "$0" "$_self_copy"
    chmod +x "$_self_copy"
    BUDSMET_REEXEC=1 BUDSMET_SELF_COPY="$_self_copy" exec "$_self_copy" "$@"
fi
# Копія прибирає себе сама. Порівнюємо точний шлях, а не шаблон імені, щоб
# не зачепити сторонній файл. Саме if, а не `&&`: при set -e хибна умова
# в кінці рядка завершила б скрипт.
if [[ -n "${BUDSMET_SELF_COPY:-}" && "$0" == "${BUDSMET_SELF_COPY}" ]]; then
    trap 'rm -f "${BUDSMET_SELF_COPY}"' EXIT
fi

APP_NAME="budsmet"
APP_DIR="/opt/${APP_NAME}/app"
VENV_DIR="/opt/${APP_NAME}/venv"
DATA_DIR="/var/lib/${APP_NAME}"
FAILED_MARK="/var/lib/${APP_NAME}/.failed-commit"

[[ $EUID -eq 0 ]] || { echo "Запустіть через sudo"; exit 1; }

git config --global --add safe.directory "$APP_DIR" 2>/dev/null || true
BRANCH="$(git -C "$APP_DIR" rev-parse --abbrev-ref HEAD)"

echo "==> Резервна копія бази перед оновленням"
"${APP_DIR}/deploy/vm/backup.sh" >/dev/null

PREVIOUS="$(git -C "$APP_DIR" rev-parse HEAD)"

echo "==> Отримання оновлень (гілка ${BRANCH})"
git -C "$APP_DIR" fetch --depth 1 origin "$BRANCH"
git -C "$APP_DIR" reset --hard "origin/${BRANCH}"
chown -R "$APP_NAME:$APP_NAME" "$APP_DIR"

echo "==> Оновлення залежностей"
"${VENV_DIR}/bin/pip" install --quiet --upgrade -r "${APP_DIR}/requirements.txt"
chown -R "$APP_NAME:$APP_NAME" "$VENV_DIR"

echo "==> Оновлення конфігурації служби та резервного копіювання"
# Розташування скриптів між версіями могло змінитись — перезаписуємо шляхи,
# інакше systemd або cron указували б на файли, яких уже немає.
sed -e "s|__USER__|${APP_NAME}|g" \
    -e "s|__APP_DIR__|${APP_DIR}|g" \
    -e "s|__VENV_DIR__|${VENV_DIR}|g" \
    -e "s|__DATA_DIR__|${DATA_DIR}|g" \
    -e "s|__PORT__|8000|g" \
    "${APP_DIR}/deploy/vm/budsmet.service" > "/etc/systemd/system/${APP_NAME}.service"
cat > "/etc/cron.d/${APP_NAME}-backup" <<CRON
# Щоденна резервна копія бази кошторисів о 03:30. Зберігається 14 останніх копій.
30 3 * * * root ${APP_DIR}/deploy/vm/backup.sh >/dev/null 2>&1
CRON
chmod 644 "/etc/cron.d/${APP_NAME}-backup"
systemctl daemon-reload

healthy() {
    # Застосунок піднімається за секунди, але на слабкій машині буває довше.
    for _ in $(seq 1 15); do
        if curl -fs --max-time 5 http://127.0.0.1:8000/api/health >/dev/null 2>&1; then
            return 0
        fi
        sleep 2
    done
    return 1
}

echo "==> Перезапуск служби"
systemctl restart "$APP_NAME"

if healthy; then
    rm -f "$FAILED_MARK"
    echo "✓ Оновлено. Версія: $(git -C "$APP_DIR" rev-parse --short HEAD)"
    echo "  База даних не змінювалась: ${DATA_DIR}/budsmet.db"
    exit 0
fi

# Запам'ятовуємо непрацездатну версію, щоб автооновлення не тягнуло її по колу.
git -C "$APP_DIR" rev-parse HEAD > "$FAILED_MARK"

# Невдале оновлення не має лишати застосунок лежачим: повертаємо попередню
# версію коду разом із її залежностями. База при цьому не чіпається.
echo "✗ Застосунок не відповідає після оновлення. Повертаю версію ${PREVIOUS:0:7}"
journalctl -u "$APP_NAME" -n 30 --no-pager || true
git -C "$APP_DIR" reset --hard "$PREVIOUS"
chown -R "$APP_NAME:$APP_NAME" "$APP_DIR"
"${VENV_DIR}/bin/pip" install --quiet -r "${APP_DIR}/requirements.txt" || true
chown -R "$APP_NAME:$APP_NAME" "$VENV_DIR"
systemctl restart "$APP_NAME"

if healthy; then
    echo "✓ Відкат виконано, працює версія $(git -C "$APP_DIR" rev-parse --short HEAD)."
    echo "  Нову версію не встановлено — дивіться журнал вище."
else
    echo "✗ Застосунок не піднявся навіть після відкоту. Потрібне втручання:"
    echo "    sudo journalctl -u ${APP_NAME} -n 80 --no-pager"
fi
exit 1
