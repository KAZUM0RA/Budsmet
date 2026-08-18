#!/usr/bin/env bash
#
# Створення безкоштовної віртуальної машини Google Cloud Always Free.
# Запускати в Cloud Shell (термінал у браузері: кнопка ">_" вгорі консолі GCP)
# або локально, якщо встановлено gcloud.
#
#   ./create-vm.sh                       # us-central1-a, машина «budsmet»
#   ./create-vm.sh --zone us-west1-b     # інша зона
#
set -euo pipefail

NAME="budsmet"
ZONE="us-central1-a"
DISK_GB="30"
MACHINE="e2-micro"
STATIC_IP="yes"

# Always Free поширюється лише на ці три регіони; в решті машина буде платною.
FREE_REGIONS="us-west1 us-central1 us-east1"
# Кандидати образів від найновішого; беремо перший, що існує.
IMAGE_FAMILIES="ubuntu-2404-lts-amd64 ubuntu-2404-lts ubuntu-2204-lts"
IMAGE_PROJECT="ubuntu-os-cloud"

log()  { printf '\n\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[✗]\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
    cat <<USAGE
Використання: ./create-vm.sh [опції]

  --name <ім'я>     назва машини (типово: ${NAME})
  --zone <зона>     зона (типово: ${ZONE}); безкоштовні регіони: ${FREE_REGIONS}
  --disk <ГБ>       розмір диска (типово: ${DISK_GB}, більше — платно)
  --no-static-ip    не закріплювати зовнішню адресу
USAGE
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --name) NAME="${2:-}"; shift 2 ;;
        --zone) ZONE="${2:-}"; shift 2 ;;
        --disk) DISK_GB="${2:-}"; shift 2 ;;
        --no-static-ip) STATIC_IP="no"; shift ;;
        -h|--help) usage; exit 0 ;;
        *) die "Невідома опція: $1 (--help для довідки)" ;;
    esac
done

command -v gcloud >/dev/null 2>&1 \
    || die "Не знайдено gcloud. Найпростіше — запустити цей скрипт у Cloud Shell: кнопка «>_» у консолі GCP."

PROJECT="$(gcloud config get-value project 2>/dev/null || true)"
[[ -n "$PROJECT" && "$PROJECT" != "(unset)" ]] \
    || die "Не обрано проєкт. Виконайте: gcloud config set project НАЗВА_ПРОЄКТУ"

REGION="${ZONE%-*}"
if ! printf '%s\n' $FREE_REGIONS | grep -qx "$REGION"; then
    warn "Регіон ${REGION} НЕ входить у безкоштовний тариф — за машину зніматимуть гроші."
    warn "Безкоштовні: ${FREE_REGIONS}."
    read -rp "Все одно продовжити? Введіть «так»: " answer
    [[ "$answer" == "так" ]] || exit 0
fi
[[ "$DISK_GB" -le 30 ]] || warn "Диск понад 30 ГБ виходить за межі безкоштовного тарифу."

log "Проєкт: ${PROJECT} · зона: ${ZONE} · машина: ${MACHINE} · диск: ${DISK_GB} ГБ"

log "Вмикання Compute Engine API (може зайняти хвилину)"
gcloud services enable compute.googleapis.com --quiet

log "Пошук актуального образу Ubuntu"
IMAGE_FAMILY=""
for family in $IMAGE_FAMILIES; do
    if gcloud compute images describe-from-family "$family" \
            --project="$IMAGE_PROJECT" --format="value(name)" >/dev/null 2>&1; then
        IMAGE_FAMILY="$family"
        break
    fi
done
[[ -n "$IMAGE_FAMILY" ]] || die "Не знайдено жодного образу Ubuntu серед: ${IMAGE_FAMILIES}"
echo "    образ: ${IMAGE_FAMILY}"

log "Правила фаєрвола для HTTP і HTTPS"
for rule in "allow-http:80:http-server" "allow-https:443:https-server"; do
    IFS=':' read -r rule_name port tag <<< "$rule"
    if gcloud compute firewall-rules describe "budsmet-${rule_name}" >/dev/null 2>&1; then
        echo "    ${rule_name}: вже існує"
    else
        gcloud compute firewall-rules create "budsmet-${rule_name}" \
            --allow="tcp:${port}" --source-ranges="0.0.0.0/0" --target-tags="$tag" \
            --description="Budsmet: доступ до веб-інтерфейсу" --quiet
        echo "    ${rule_name}: створено"
    fi
done

if gcloud compute instances describe "$NAME" --zone="$ZONE" >/dev/null 2>&1; then
    warn "Машина «${NAME}» у зоні ${ZONE} вже існує — створення пропущено."
else
    log "Створення машини «${NAME}»"
    gcloud compute instances create "$NAME" \
        --zone="$ZONE" \
        --machine-type="$MACHINE" \
        --image-family="$IMAGE_FAMILY" \
        --image-project="$IMAGE_PROJECT" \
        --boot-disk-size="${DISK_GB}GB" \
        --boot-disk-type="pd-standard" \
        --tags="http-server,https-server" \
        --description="Budsmet — складання будівельних кошторисів" \
        --quiet
fi

if [[ "$STATIC_IP" == "yes" ]]; then
    log "Закріплення зовнішньої адреси"
    # Без цього після перезапуску машини адреса зміниться і домен перестане на неї вказувати.
    if gcloud compute addresses describe "${NAME}-ip" --region="$REGION" >/dev/null 2>&1; then
        echo "    адресу вже закріплено"
    else
        CURRENT_IP="$(gcloud compute instances describe "$NAME" --zone="$ZONE" \
            --format='get(networkInterfaces[0].accessConfigs[0].natIP)')"
        if gcloud compute addresses create "${NAME}-ip" --region="$REGION" \
                --addresses="$CURRENT_IP" --quiet 2>/dev/null; then
            echo "    закріплено поточну адресу ${CURRENT_IP}"
        else
            warn "Не вдалось закріпити адресу автоматично."
            warn "Зробіть це в консолі: VPC network → IP addresses → Reserve."
        fi
    fi
fi

IP="$(gcloud compute instances describe "$NAME" --zone="$ZONE" \
    --format='get(networkInterfaces[0].accessConfigs[0].natIP)')"

cat <<SUMMARY

────────────────────────────────────────────────────────────────
  Машину створено
────────────────────────────────────────────────────────────────
  Назва          ${NAME}
  Зона           ${ZONE}
  Зовнішня IP    ${IP}
────────────────────────────────────────────────────────────────

  Далі:

  1) Впишіть IP ${IP} у свій піддомен на duckdns.org

  2) Підключіться до машини:
       gcloud compute ssh ${NAME} --zone=${ZONE}

  3) На машині виконайте встановлення (підставте свій домен і логін):
       sudo apt-get update && sudo apt-get install -y git && \\
       sudo git clone --depth 1 --branch claude/web-app-cost-estimates-l1x9ef \\
         https://github.com/KAZUM0RA/Budsmet.git /opt/budsmet/app && \\
       sudo /opt/budsmet/app/deploy/vm/setup.sh \\
         --domain ВАШ.duckdns.org --login shef --email ВАША@пошта

SUMMARY
