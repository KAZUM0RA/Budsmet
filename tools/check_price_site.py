#!/usr/bin/env python3
"""Перевірка розбору прайс-листа сайту.

Запускати там, де є доступ до сайту (наприклад, на сервері):

    python3 tools/check_price_site.py
    python3 tools/check_price_site.py --dump /tmp/price.html
    python3 tools/check_price_site.py https://інший.сайт/ціни --pages 5

Показує, що саме вдалось розібрати, а якщо не вдалось — визначає причину:
чи є ціни в самому HTML, чи їх підвантажує скрипт уже в браузері.
"""
import argparse
import collections
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import config  # noqa: E402
from backend.services.price_sites import (_RowExtractor, _fetch,  # noqa: E402
                                          fetch_site, parse_rows)

_PRICE_HINT = re.compile(r"\d[\d\s]{0,7}\s*(?:грн|₴)", re.I)
_UNIT_HINT = re.compile(r"\b(м2|м²|кв\.?\s?м|шт|м\.?п|пог\.?\s?м)\b", re.I)


def diagnose(url: str, dump: str | None) -> None:
    """Чому нічого не знайшлось: немає цін у HTML чи не розпізнано рядки."""
    print("\n── Діагностика ──")
    try:
        html = _fetch(url)
    except Exception as exc:
        print(f"  Сторінку не вдалося завантажити: {type(exc).__name__}: {exc}")
        return

    print(f"  Розмір сторінки: {len(html):,} символів".replace(",", " "))
    if dump:
        Path(dump).write_text(html, encoding="utf-8")
        print(f"  HTML збережено: {dump}")

    prices = _PRICE_HINT.findall(html)
    print(f"  Згадок ціни в гривнях у HTML: {len(prices)}")
    print(f"  Згадок одиниць виміру: {len(_UNIT_HINT.findall(html))}")

    if not prices:
        print("\n  ВИСНОВОК: цін у HTML немає взагалі.")
        print("  Найімовірніше, сторінка підвантажує їх скриптом уже в браузері.")
        print("  Такий сайт цим способом не розібрати — потрібен інший підхід.")
        print("  Надішліть цей вивід розробнику.")
        return

    parser = _RowExtractor()
    parser.feed(html)
    parser.close()
    print(f"  Блоків, схожих на рядки: {len(parser.rows)}")

    with_price = [cells for cells, _cat in parser.rows
                  if any(_PRICE_HINT.search(c) for c in cells)]
    print(f"  З них містять ціну: {len(with_price)}")

    print("\n  Приклади рядків із ціною (як їх бачить розбірник):")
    for cells in with_price[:8]:
        print("    " + " | ".join(c[:38] for c in cells[:5]))
    if not with_price:
        print("\n  Рядки з цінами не виділились. Приклади тексту навколо цін:")
        for match in list(_PRICE_HINT.finditer(html))[:6]:
            chunk = re.sub(r"<[^>]+>", " ", html[max(0, match.start() - 130):match.end() + 40])
            print("    …" + re.sub(r"\s+", " ", chunk).strip()[:150])
    print("\n  Надішліть цей вивід розробнику — цього достатньо, щоб доналаштувати розбір.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Перевірка прайс-листа сайту")
    parser.add_argument("url", nargs="?", default=config.PRICE_SITE)
    parser.add_argument("--pages", type=int, default=20, help="скільки сторінок обійти")
    parser.add_argument("--show", type=int, default=25, help="скільки зразків показати")
    parser.add_argument("--dump", metavar="ФАЙЛ", help="зберегти HTML сторінки у файл")
    args = parser.parse_args()

    if not args.url:
        print("Адресу прайса не задано (BUDSMET_PRICE_SITE).")
        return 1

    print(f"Завантажую {args.url} …")
    try:
        prices, pages = fetch_site(args.url, max_pages=args.pages)
    except Exception as exc:
        print(f"✗ Не вдалося завантажити: {type(exc).__name__}: {exc}")
        return 1

    print(f"\nСторінок оброблено: {len(pages)}")
    print(f"Позицій розібрано:  {len(prices)}")

    if not prices:
        print("\n✗ Жодної позиції «робота — одиниця — ціна» не знайдено.")
        diagnose(args.url, args.dump)
        return 1

    units = collections.Counter(p.unit or "—" for p in prices)
    print(f"\nОдиниці виміру: {dict(units.most_common(8))}")

    by_category = collections.Counter(p.category or "(без розділу)" for p in prices)
    print("\nРозділи:")
    for name, count in by_category.most_common(15):
        print(f"  {count:5}  {name[:70]}")

    values = sorted(p.price for p in prices)
    print(f"\nЦіни: від {values[0]:.0f} до {values[-1]:.0f} грн, "
          f"медіана {values[len(values) // 2]:.0f}")

    print(f"\nЗразки ({min(args.show, len(prices))}):")
    for price in prices[: args.show]:
        print(f"  {price.price:>9.2f} грн / {price.unit or '?':<6} {price.name[:64]}")

    suspicious = [p for p in prices if p.price < 5 or len(p.name) < 10 or not p.unit]
    if suspicious:
        print(f"\n⚠ Підозрілих рядків: {len(suspicious)} (перші 5):")
        for price in suspicious[:5]:
            print(f"  {price.price:>9.2f} грн / {price.unit or '?':<6} {price.name[:64]}")

    if args.dump:
        Path(args.dump).write_text(_fetch(args.url), encoding="utf-8")
        print(f"\nHTML збережено: {args.dump}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
