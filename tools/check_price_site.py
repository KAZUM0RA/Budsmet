#!/usr/bin/env python3
"""Перевірка розбору прайс-листа сайту.

Запускати там, де є доступ до сайту (наприклад, на сервері):

    python3 tools/check_price_site.py
    python3 tools/check_price_site.py https://інший.сайт/ціни --pages 5

Показує, скільки позицій розібрано, за категоріями та зразки рядків — щоб
одразу видно було, чи парсер зрозумів верстку сайту.
"""
import argparse
import collections
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import config  # noqa: E402
from backend.services.price_sites import fetch_site  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Перевірка прайс-листа сайту")
    parser.add_argument("url", nargs="?", default=config.PRICE_SITE)
    parser.add_argument("--pages", type=int, default=20, help="скільки сторінок обійти")
    parser.add_argument("--show", type=int, default=25, help="скільки зразків показати")
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
        print("  Ймовірно, ціни підвантажуються скриптом або верстка незвична.")
        return 1

    units = collections.Counter(p.unit or "—" for p in prices)
    print(f"\nОдиниці виміру: {dict(units.most_common(8))}")

    by_category = collections.Counter(p.category or "(без розділу)" for p in prices)
    print("\nРозділи:")
    for name, count in by_category.most_common(15):
        print(f"  {count:5}  {name[:70]}")

    prices_sorted = sorted(p.price for p in prices)
    middle = prices_sorted[len(prices_sorted) // 2]
    print(f"\nЦіни: від {prices_sorted[0]:.0f} до {prices_sorted[-1]:.0f} грн, медіана {middle:.0f}")

    print(f"\nЗразки ({min(args.show, len(prices))}):")
    for price in prices[: args.show]:
        print(f"  {price.price:>9.2f} грн / {price.unit or '?':<6} {price.name[:64]}")

    suspicious = [p for p in prices if p.price < 5 or len(p.name) < 10]
    if suspicious:
        print(f"\n⚠ Підозрілих рядків: {len(suspicious)} (перші 5):")
        for price in suspicious[:5]:
            print(f"  {price.price:>9.2f} грн / {price.unit or '?':<6} {price.name[:64]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
