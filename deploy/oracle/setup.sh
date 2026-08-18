#!/usr/bin/env bash
#
# Розгортання Budsmet на віртуальній машині Oracle Cloud Always Free.
# Скрипт ідемпотентний: його можна запускати повторно.
#
# Приклад:
#   sudo ./setup.sh --domain koshtorys.duckdns.org --login shef
#   sudo ./setup.sh --login shef            # без домену, доступ за IP і без HTTPS
#
set -euo pipefail

APP_NAME="budsmet"
REPO_URL="https://github.com/KAZUM0RA/Budsmet.git"
BRANCH="claude/web-app-cost-estimates-l1x9ef"
BASE_DIR="/opt/${APP_NAME}"
APP_DIR="${BASE_DIR}/app"
VENV_DIR="${BASE_DIR}/venv"
DATA_DIR="/var/lib/${APP_NAME}"
PORT="8000"
DOMAIN=""
LOGIN=""
PASSWORD=""
EMAIL=""
SKIP_TLS="no"

log()  { printf '\n\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[✗]\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
    cat <<USAGE
Використання: sudo ./setup.sh [опції]

  --domain <домен>    доменне ім'я для HTTPS (напр. koshtorys.duckdns.org)
  --email  <пошта>    пошта для сповіщень Let's Encrypt про закінчення сертифіката
  --login  <логін>    логін для входу в застосунок (типово: budsmet)
  --password <пароль> пароль; якщо не вказано — буде згенеровано
  --port   <порт>     внутрішній порт застосунку (типово: 8000)
  --branch <гілка>    гілка репозиторію (типово: ${BRANCH})
  --skip-tls          не отримувати сертифікат навіть за наявності домену
USAGE
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --domain)   DOMAIN="${2:-}"; shift 2 ;;
        --email)    EMAIL="${2:-}"; shift 2 ;;
        --login)    LOGIN="${2:-}"; shift 2 ;;
        --password) PASSWORD="${2:-}"; shift 2 ;;
        --port)     PORT="${2:-}"; shift 2 ;;
        --branch)   BRANCH="${2:-}"; shift 2 ;;
        --skip-tls) SKIP_TLS="yes"; shift ;;
        -h|--help)  usage; exit 0 ;;
        *)          die "Невідома опція: $1 (--help для довідки)" ;;
    esac
done

[[ $EUID -eq 0 ]] || die "Запустіть через sudo: sudo ./setup.sh …"
command -v apt-get >/dev/null 2>&1 \
    || die "Скрипт розрахований на Ubuntu. Пересоздайте машину з образом Canonical Ubuntu 24.04."
LOGIN="${LOGIN:-budsmet}"

# ---------------------------------------------------------------- пакети
log "Встановлення системних пакетів"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
# fonts-dejavu-core потрібен для кирилиці у PDF-вивантаженні;
# build-essential і python3-dev — страховка, якщо для ARM не знайдеться готового колеса.
apt-get install -y -qq \
    python3 python3-venv python3-dev build-essential \
    git nginx apache2-utils curl ca-certificates fonts-dejavu-core

# ------------------------------------------------------------ користувач
if ! id -u "$APP_NAME" >/dev/null 2>&1; then
    log "Створення системного користувача ${APP_NAME}"
    useradd --system --home-dir "$BASE_DIR" --shell /usr/sbin/nologin "$APP_NAME"
fi
install -d -o "$APP_NAME" -g "$APP_NAME" -m 750 "$BASE_DIR" "$DATA_DIR"

# --------------------------------------------------------------- код
# Код належить користувачу budsmet, а git запускається від root — без цього
# git відмовиться працювати з помилкою «detected dubious ownership».
git config --global --add safe.directory "$APP_DIR" 2>/dev/null || true

if [[ -d "${APP_DIR}/.git" ]]; then
    log "Оновлення коду з репозиторію"
    git -C "$APP_DIR" remote set-url origin "$REPO_URL"
    git -C "$APP_DIR" fetch --depth 1 origin "$BRANCH"
    git -C "$APP_DIR" checkout -B "$BRANCH" "origin/${BRANCH}"
else
    log "Клонування репозиторію"
    rm -rf "$APP_DIR"
    git clone --depth 1 --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
fi
chown -R "$APP_NAME:$APP_NAME" "$APP_DIR"

# ------------------------------------------------------- залежності Python
log "Встановлення залежностей Python"
[[ -d "$VENV_DIR" ]] || python3 -m venv "$VENV_DIR"
"${VENV_DIR}/bin/pip" install --quiet --upgrade pip wheel
"${VENV_DIR}/bin/pip" install --quiet -r "${APP_DIR}/requirements.txt"
chown -R "$APP_NAME:$APP_NAME" "$VENV_DIR"

# Перевірка, що кирилиця у PDF працюватиме (інакше вивантаження буде нечитабельним).
if ! "${VENV_DIR}/bin/python" - "$APP_DIR" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
from backend.services.exporter import _register_font
regular, _bold = _register_font()
print(f"    шрифт для PDF: {regular}")
sys.exit(0 if regular != "Helvetica" else 1)
PY
then
    warn "Не знайдено шрифту з кирилицею — PDF-вивантаження буде нечитабельним."
    warn "Виправлення: sudo apt-get install -y fonts-dejavu-core && sudo systemctl restart ${APP_NAME}"
fi

# ------------------------------------------------------------- systemd
log "Налаштування служби systemd"
sed -e "s|__USER__|${APP_NAME}|g" \
    -e "s|__APP_DIR__|${APP_DIR}|g" \
    -e "s|__VENV_DIR__|${VENV_DIR}|g" \
    -e "s|__DATA_DIR__|${DATA_DIR}|g" \
    -e "s|__PORT__|${PORT}|g" \
    "${APP_DIR}/deploy/oracle/budsmet.service" > "/etc/systemd/system/${APP_NAME}.service"
touch /etc/budsmet.env
chmod 600 /etc/budsmet.env
systemctl daemon-reload
systemctl enable --now "$APP_NAME"

# --------------------------------------------------------------- пароль
if [[ ! -f /etc/nginx/budsmet.htpasswd ]]; then
    if [[ -z "$PASSWORD" ]]; then
        PASSWORD="$(tr -dc 'A-Za-z0-9' </dev/urandom | head -c 14)"
        GENERATED="yes"
    fi
    log "Створення логіна для входу"
    htpasswd -bc /etc/nginx/budsmet.htpasswd "$LOGIN" "$PASSWORD" >/dev/null 2>&1
    chown root:www-data /etc/nginx/budsmet.htpasswd
    chmod 640 /etc/nginx/budsmet.htpasswd
elif [[ -n "$PASSWORD" ]]; then
    log "Оновлення пароля користувача ${LOGIN}"
    htpasswd -b /etc/nginx/budsmet.htpasswd "$LOGIN" "$PASSWORD" >/dev/null 2>&1
fi

# ----------------------------------------------------------------- nginx
log "Налаштування nginx"
SERVER_NAME="${DOMAIN:-_}"
sed -e "s|__SERVER_NAME__|${SERVER_NAME}|g" -e "s|__PORT__|${PORT}|g" \
    "${APP_DIR}/deploy/oracle/nginx-budsmet.conf" > "/etc/nginx/sites-available/${APP_NAME}"
# Якщо на машині вимкнено IPv6, рядок «listen [::]:80» не дасть nginx запуститись.
if [[ ! -f /proc/net/if_inet6 ]]; then
    sed -i '/listen \[::\]:/d' "/etc/nginx/sites-available/${APP_NAME}"
    echo "    IPv6 вимкнено — слухаємо лише IPv4"
fi
ln -sf "/etc/nginx/sites-available/${APP_NAME}" "/etc/nginx/sites-enabled/${APP_NAME}"
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx

# ------------------------------------------------------------- фаєрвол
# На образах Oracle усі порти, крім 22, закриті локальними правилами —
# це найчастіша причина, чому сайт «не відкривається» після налаштування.
log "Відкриття портів 80 і 443 на самій машині"
if command -v firewall-cmd >/dev/null 2>&1 && systemctl is-active --quiet firewalld; then
    firewall-cmd --permanent --add-service=http  >/dev/null
    firewall-cmd --permanent --add-service=https >/dev/null
    firewall-cmd --reload >/dev/null
    echo "    firewalld: порти відкрито"
elif command -v iptables >/dev/null 2>&1; then
    # Помилка тут не має зривати вже виконане встановлення — лише попереджаємо.
    if (
        set -e
        for p in 80 443; do
            iptables -C INPUT -p tcp --dport "$p" -m conntrack --ctstate NEW -j ACCEPT 2>/dev/null \
                || iptables -I INPUT 6 -p tcp --dport "$p" -m conntrack --ctstate NEW -j ACCEPT
        done
    ); then
        command -v netfilter-persistent >/dev/null 2>&1 \
            || apt-get install -y -qq iptables-persistent >/dev/null 2>&1 || true
        netfilter-persistent save >/dev/null 2>&1 || true
        echo "    iptables: порти відкрито і збережено"
    else
        warn "Не вдалось змінити правила iptables — відкрийте порти 80 і 443 вручну:"
        warn "  sudo iptables -I INPUT 6 -p tcp --dport 80 -m conntrack --ctstate NEW -j ACCEPT"
    fi
else
    warn "Не вдалось налаштувати фаєрвол — відкрийте порти 80 і 443 вручну."
fi

# ------------------------------------------------- щоденна резервна копія
log "Налаштування щоденної резервної копії"
cat > /etc/cron.d/${APP_NAME}-backup <<CRON
# Щоденна резервна копія бази кошторисів о 03:30. Зберігається 14 останніх копій.
30 3 * * * root ${APP_DIR}/deploy/oracle/backup.sh >/dev/null 2>&1
CRON
chmod 644 "/etc/cron.d/${APP_NAME}-backup"

# -------------------------------------------------------------- HTTPS
if [[ -n "$DOMAIN" && "$SKIP_TLS" == "no" ]]; then
    log "Отримання сертифіката Let's Encrypt для ${DOMAIN}"
    apt-get install -y -qq certbot python3-certbot-nginx
    CERTBOT_ARGS=(--nginx -d "$DOMAIN" --agree-tos --non-interactive --redirect)
    if [[ -n "$EMAIL" ]]; then CERTBOT_ARGS+=(-m "$EMAIL"); else CERTBOT_ARGS+=(--register-unsafely-without-email); fi
    if certbot "${CERTBOT_ARGS[@]}"; then
        systemctl reload nginx
    else
        warn "Сертифікат не отримано. Найчастіші причини:"
        warn "  • домен ще не вказує на IP цієї машини (перевірте: dig +short ${DOMAIN});"
        warn "  • порт 80 закритий у Security List віртуальної мережі в консолі Oracle."
        warn "Після усунення повторіть: sudo certbot --nginx -d ${DOMAIN}"
    fi
fi

# -------------------------------------------------------------- підсумок
IP="$(curl -fsS --max-time 5 https://api.ipify.org 2>/dev/null || hostname -I | awk '{print $1}')"
URL="http://${DOMAIN:-$IP}"
[[ -n "$DOMAIN" && "$SKIP_TLS" == "no" ]] && URL="https://${DOMAIN}"

sleep 2
if curl -fsS --max-time 10 "http://127.0.0.1:${PORT}/api/health" >/dev/null; then
    STATUS="працює"
else
    STATUS="НЕ ВІДПОВІДАЄ — дивіться: journalctl -u ${APP_NAME} -n 50"
fi

cat <<SUMMARY

────────────────────────────────────────────────────────────────
  Budsmet розгорнуто
────────────────────────────────────────────────────────────────
  Адреса        ${URL}
  Логін         ${LOGIN}
  Пароль        ${PASSWORD:-(не змінювався)}
  Стан служби   ${STATUS}
  База даних    ${DATA_DIR}/budsmet.db
────────────────────────────────────────────────────────────────
  Журнал        journalctl -u ${APP_NAME} -f
  Перезапуск    sudo systemctl restart ${APP_NAME}
  Оновлення     sudo ${APP_DIR}/deploy/oracle/update.sh
  Резервна копія sudo ${APP_DIR}/deploy/oracle/backup.sh
────────────────────────────────────────────────────────────────
SUMMARY

if [[ "${GENERATED:-no}" == "yes" ]]; then
    warn "Пароль згенеровано автоматично — збережіть його зараз, він більше не покажеться."
fi
if [[ -z "$DOMAIN" ]]; then
    warn "Працює без HTTPS: пароль передається у відкритому вигляді."
    warn "Заведіть безкоштовний домен (напр. на duckdns.org) і перезапустіть скрипт з --domain."
fi
