# Розгортання Budsmet на Google Cloud Always Free

Запасний варіант, якщо реєстрація в Oracle не проходить. Скрипти ті самі —
[`deploy/vm/`](../vm), відрізняються лише кроки в консолі.

**Що дає безкоштовний тариф:** одна віртуальна машина `e2-micro` (1 ГБ пам'яті)
з диском 30 ГБ, яка працює цілодобово і безкоштовно, без обмеження в часі.

**Головне обмеження:** безкоштовна машина доступна **лише у трьох регіонах США** —
`us-west1` (Орегон), `us-central1` (Айова), `us-east1` (Південна Кароліна).
В Європі та Азії `e2-micro` платна. Для цього застосунку затримка близько
120–150 мс непомітна: це не гра, а форма з таблицею.

**Картка потрібна** для верифікації, як і в Oracle. Google списує і повертає
близько $1. Google дає ще $300 пробних коштів на 90 днів — Always Free працює
далі й після їх завершення, головне не вмикати платний акаунт без потреби.

---

## Крок 1. Реєстрація

<https://console.cloud.google.com> → увійдіть акаунтом Google → **Try for free**.
Заповніть анкету, додайте картку. Далі створіть проєкт (**Select a project → New
project**), назвіть `budsmet`.

## Крок 2. Ключ для входу

Якщо ви вже створювали ключ за інструкцією для Oracle — використовуйте його.
Інакше на своєму комп'ютері:

```bash
ssh-keygen -t ed25519 -C "budsmet"
cat ~/.ssh/id_ed25519.pub          # Windows: type $env:USERPROFILE\.ssh\id_ed25519.pub
```

## Крок 3. Створення машини

**☰ Menu → Compute Engine → VM instances → Create instance**.

| Поле | Значення |
|---|---|
| Name | `budsmet` |
| Region | `us-central1` (або `us-west1` / `us-east1` — тільки ці три) |
| Machine configuration | **E2** → **e2-micro** |
| Boot disk → Change | **Ubuntu 24.04 LTS**, диск **30 GB standard persistent disk** |
| Firewall | ✅ **Allow HTTP traffic** і ✅ **Allow HTTPS traffic** |
| Security → Manage Access | **Add manually generated SSH keys** → вставте вміст `id_ed25519.pub` |

Праворуч у формі має бути напис, що конфігурація підпадає під **free tier**.
Натисніть **Create** і запишіть **External IP**.

> На відміну від Oracle, окремо відкривати порти не треба — це роблять галочки
> **Allow HTTP/HTTPS traffic**. Якщо ви їх пропустили: **VPC network → Firewall →
> Create firewall rule**, `0.0.0.0/0`, TCP `80,443`.

> **Зробіть External IP статичною**, інакше після перезапуску машини адреса
> зміниться і домен перестане на неї вказувати: **VPC network → IP addresses** →
> навпроти вашої адреси **Reserve**. У межах безкоштовного тарифу статична
> адреса безкоштовна, поки прив'язана до працюючої машини.

## Крок 4. Домен

Так само, як в інструкції для Oracle: заведіть безкоштовний піддомен на
<https://www.duckdns.org> і впишіть у нього External IP машини.

## Крок 5. Встановлення

```bash
ssh ubuntu@ВАШ_EXTERNAL_IP
```

```bash
sudo apt-get update && sudo apt-get install -y git && \
sudo git clone --depth 1 --branch claude/web-app-cost-estimates-l1x9ef \
  https://github.com/KAZUM0RA/Budsmet.git /opt/budsmet/app && \
sudo /opt/budsmet/app/deploy/vm/setup.sh \
  --domain koshtorys.duckdns.org \
  --login shef \
  --email ваша@пошта
```

Далі все як в [інструкції для Oracle](../oracle/README.md#обслуговування):
оновлення, резервні копії, відновлення, журнали — тими самими командами.

---

## Особливості цієї машини

**1 ГБ пам'яті замість 12.** Застосунку цього вистачає з запасом: заміряно
72 МБ у спокої і 88 МБ на піку, коли підряд обробляються відомості на 116 і
129 позицій із вивантаженням у PDF та Excel. Тісно буде не застосунку, а
встановленню пакетів, тож одразу додайте файл підкачки:

```bash
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && \
sudo mkswap /swapfile && sudo swapon /swapfile && \
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

**Стежте за витратами трафіку.** Безкоштовно віддається 200 ГБ вихідного
трафіку на місяць (окрім Китаю та Австралії). Для кошторисів це недосяжна межа,
але якщо колись роздаватимете з цієї машини щось важче — перевіряйте
**Billing → Reports**.

**Поставте бюджетне сповіщення.** **Billing → Budgets & alerts → Create budget**,
сума $1, сповіщення на 100%. Тоді ви одразу дізнаєтесь, якщо щось випадково
вийде за межі безкоштовного тарифу.
