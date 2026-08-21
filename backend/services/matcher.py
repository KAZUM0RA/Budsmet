"""Співставлення довільного найменування роботи з позицією довідника розцінок."""
from __future__ import annotations

from dataclasses import dataclass

import math
import re

from rapidfuzz import fuzz

from .normalize import normalize_name, normalize_unit, stem, stem_phrase

# Дія над конструкцією. Сплутати демонтаж із монтажем — найдорожча помилка
# підбору, тому клас дії перевіряється окремо від схожості тексту.
_ACTIONS = {
    "demolition": ("демонтаж", "розбиран", "зніман", "знят", "видален", "прибиран",
                   "зрубуван", "збиван", "пробиван", "штроблен", "свердлен"),
    "install": ("улаштуван", "влаштуван", "установлен", "установк", "монтаж", "укладан",
                "укладк", "мурував", "мурован", "заповнен", "обклеюван", "поклейк",
                "фарбуван", "штукатурен", "шпаклюван", "шпаклівк", "опорядж",
                "утеплен", "ізоляц", "прокладан", "прокладк", "armуван", "армуван",
                "накритт", "ґрунтуван", "грунтуван", "затирк", "обшивк", "заміна",
                "замін", "встановлен"),
}


# Плаский перелік маркерів дії — щоб не зважувати дієслова як іменники.
_ACTION_MARKERS = tuple(sorted({m for markers in _ACTIONS.values() for m in markers},
                               key=len, reverse=True))


def _is_action_word(token: str) -> bool:
    """Чи є слово позначенням дії («демонтаж», «заміна», «улаштування»)."""
    return any(token.startswith(marker[:5]) for marker in _ACTION_MARKERS
               if len(marker) >= 5)


def _action_class(text: str) -> str:
    """До якого класу робіт належить назва: демонтаж, монтаж чи невизначено."""
    low = normalize_name(text)
    for name, markers in _ACTIONS.items():
        if any(marker in low for marker in markers):
            return name
    return ""


@dataclass
class MatchResult:
    code: str
    name: str
    unit: str
    score: float          # 0..100
    item: dict


def work_key(name: str, unit: str = "") -> str:
    """Стабільний ключ роботи для історії цін та кешу."""
    base = stem_phrase(name)
    unit = normalize_unit(unit)
    return f"{base}|{unit}" if unit else base


class WorkMatcher:
    """Підбір розцінки за назвою роботи, стійкий до різних формулювань.

    Оцінка будується не на загальній схожості рядків — вона завищує вагу
    початкового слова («Демонтаж…», «Установлення…»), яке є в десятках
    розцінок. Натомість кожне слово запиту важить тим більше, чим рідше воно
    трапляється в довіднику: «плитк» або «шпалер» вирішують, а «робіт» — ні.
    """

    # Нижче цього порогу вважаємо, що розцінку не знайдено.
    MIN_SCORE = 62.0
    # Поріг для пошуку в інтерфейсі (нижче — вважаємо збіг випадковим).
    SEARCH_MIN_SCORE = 55.0
    # Збіг за одиницею виміру дає бонус, розбіжність — штраф.
    UNIT_BONUS = 6.0
    UNIT_PENALTY = 14.0
    # Демонтаж проти монтажу — різні роботи навіть за майже однакової назви,
    # тому в автоматичному підборі протилежний клас не розглядається взагалі,
    # а в переліку варіантів для ручного вибору сильно знижується.
    ACTION_PENALTY = 45.0

    def __init__(self, items: list[dict]):
        self.items = items
        self._exact: dict[str, dict] = {}
        self._index: list[dict] = []
        self._choices: list[str] = []
        self._tokens: list[set[str]] = []
        self._cat_tokens: list[set[str]] = []
        self._actions: list[str] = []

        for item in items:
            key = work_key(item["name"], item.get("unit", ""))
            self._exact.setdefault(key, item)
            self._exact.setdefault(stem_phrase(item["name"]), item)
            self._index.append(item)
            self._choices.append(stem_phrase(item["name"]))
            tokens = {stem(t) for t in normalize_name(item["name"]).split()}
            self._tokens.append(tokens)
            self._cat_tokens.append(
                {stem(t) for t in normalize_name(item.get("category", "")).split()})
            self._actions.append(_action_class(item["name"]))

        self._total = max(len(items), 1)
        self._weights: dict[str, float] = {}

    def _weight(self, token: str) -> float:
        """Вага слова запиту: часте в довіднику важить мало, рідкісне — багато.

        Кількість розцінок зі словом рахується тим самим правилом, що й сам
        збіг: інакше «плитк» не знаходило б «плиток», отримувало нульову вагу
        і переставало б розрізняти роботи.

        Слово, якого немає в жодній розцінці («армстронг», «поклейка»), важить
        нуль: жоден варіант його не містить, отже воно нікого не вирізняє, а
        лише однаково штрафувало б усіх кандидатів.
        """
        cached = self._weights.get(token)
        if cached is not None:
            return cached
        found = sum(1 for words in self._tokens if self._token_hit(token, words))
        weight = math.log(1 + self._total / found) if found else 0.0
        self._weights[token] = weight
        return weight

    @staticmethod
    def _token_hit(query_token: str, words: set[str]) -> bool:
        """Чи є слово запиту в назві розцінки з поправкою на українські відмінки.

        Проста перевірка входження підрядка тут не працює: «розетк» не міститься
        в «розеток», а «плитк» — у «плиток» (випадний голосний).
        """
        if not query_token:
            return False
        for word in words:
            # Довше слово запиту не має «поглинати» коротше слово розцінки:
            # інакше «плитк» (плитка) збігалося б із «плит» (плита перекриття).
            if word.startswith(query_token) and len(query_token) >= 4:
                return True
            if query_token.startswith(word) and len(word) >= 5:
                return True
            if len(query_token) >= 4 and len(word) >= 4:
                # Короткі слова легко сплутати («плитк» — плитка, «плит» —
                # плита), тому для них потрібен вищий поріг схожості.
                threshold = 90 if min(len(query_token), len(word)) < 6 else 82
                if fuzz.ratio(query_token, word) >= threshold:
                    return True
        return False

    def _coverage(self, query_tokens: list[str], idx: int) -> float:
        """Частка ваги слів запиту, знайдених у назві розцінки (0..1).

        Слова-дії тут не враховуються: за них відповідає окрема перевірка класу
        робіт. Інакше «заміна» в запиті «Заміна розеток» важила б більше за
        саме «розетки» і відкидала б правильну розцінку.
        """
        query_tokens = [t for t in query_tokens if not _is_action_word(t)] or query_tokens
        if not query_tokens:
            return 0.0
        words = self._tokens[idx]
        total = sum(self._weight(t) for t in query_tokens)
        if total <= 0:
            return 0.0
        found = sum(self._weight(t) for t in query_tokens if self._token_hit(t, words))
        return found / total

    def _score(self, query_tokens: list[str], query_phrase: str, query_action: str,
               unit: str, idx: int) -> float:
        coverage = self._coverage(query_tokens, idx)
        similarity = fuzz.token_set_ratio(query_phrase, self._choices[idx]) / 100.0
        # Основа — покриття вагомих слів; схожість рядків лише уточнює.
        score = (coverage * 0.78 + similarity * 0.22) * 100.0

        item_action = self._actions[idx]
        if query_action and item_action and query_action != item_action:
            score -= self.ACTION_PENALTY

        item_unit = normalize_unit(self._index[idx].get("unit", ""))
        if unit and item_unit:
            score += self.UNIT_BONUS if unit == item_unit else -self.UNIT_PENALTY
        return score

    def match(self, name: str, unit: str = "", limit: int = 1) -> list[MatchResult]:
        if not name or not name.strip():
            return []
        unit_n = normalize_unit(unit)
        key = work_key(name, unit_n)
        hit = self._exact.get(key) or self._exact.get(stem_phrase(name))
        if hit is not None and (not unit_n or normalize_unit(hit.get("unit", "")) == unit_n):
            return [MatchResult(hit["code"], hit["name"], hit.get("unit", ""), 100.0, hit)]

        query_tokens = [stem(t) for t in normalize_name(name).split() if t]
        if not query_tokens:
            return []
        query_phrase = stem_phrase(name)
        query_action = _action_class(name)

        scored = [
            MatchResult(item["code"], item["name"], item.get("unit", ""),
                        round(min(self._score(query_tokens, query_phrase, query_action,
                                              unit_n, idx), 99.9), 1), item)
            for idx, item in enumerate(self._index)
            # Демонтажну роботу не оцінюємо за монтажною розцінкою і навпаки.
            if not (query_action and self._actions[idx]
                    and query_action != self._actions[idx])
        ]
        scored.sort(key=lambda r: (-r.score, len(r.name)))
        return [s for s in scored if s.score >= self.MIN_SCORE][:limit]

    def candidates(self, name: str, unit: str = "", limit: int = 5) -> list[MatchResult]:
        """Найкращі варіанти незалежно від порогу — для ручного вибору."""
        if not name or not name.strip():
            return []
        query_tokens = [stem(t) for t in normalize_name(name).split() if t]
        if not query_tokens:
            return []
        query_phrase = stem_phrase(name)
        query_action = _action_class(name)
        unit_n = normalize_unit(unit)
        scored = [
            MatchResult(item["code"], item["name"], item.get("unit", ""),
                        round(min(self._score(query_tokens, query_phrase, query_action,
                                              unit_n, idx), 99.9), 1), item)
            for idx, item in enumerate(self._index)
        ]
        scored.sort(key=lambda r: (-r.score, len(r.name)))
        return scored[:limit]

    def best(self, name: str, unit: str = "") -> MatchResult | None:
        found = self.match(name, unit, limit=1)
        return found[0] if found else None

    @staticmethod
    def _token_hit(query_token: str, words: set[str]) -> bool:
        """Чи є слово запиту в назві розцінки з поправкою на українські відмінки.

        Проста перевірка входження підрядка тут не працює: «розетк» не міститься
        в «розеток», а «плитк» — у «плиток» (випадний голосний).
        """
        if not query_token:
            return False
        for word in words:
            # Довше слово запиту не має «поглинати» коротше слово розцінки:
            # інакше «плитк» (плитка) збігалося б із «плит» (плита перекриття).
            if word.startswith(query_token) and len(query_token) >= 4:
                return True
            if query_token.startswith(word) and len(word) >= 5:
                return True
            if len(query_token) >= 4 and len(word) >= 4:
                # Короткі слова легко сплутати («плитк» — плитка, «плит» —
                # плита), тому для них потрібен вищий поріг схожості.
                threshold = 90 if min(len(query_token), len(word)) < 6 else 82
                if fuzz.ratio(query_token, word) >= threshold:
                    return True
        return False

    def search(self, text: str, limit: int = 20) -> list[MatchResult]:
        """Пошук для автопідказки: коротке слово має знаходити довгі назви розцінок."""
        text = (text or "").strip()
        if not text:
            return [MatchResult(i["code"], i["name"], i.get("unit", ""), 0.0, i)
                    for i in self.items[:limit]]

        query_tokens = [stem(t) for t in normalize_name(text).split() if t]
        if not query_tokens:
            return []
        query_phrase = stem_phrase(text)
        scored: list[MatchResult] = []
        for idx, item in enumerate(self._index):
            score = self._score(query_tokens, query_phrase, "", "", idx)
            # У пошуку збіг за розділом довідника теж підказка, але слабша.
            if any(self._token_hit(t, self._cat_tokens[idx]) for t in query_tokens):
                score = max(score, self.SEARCH_MIN_SCORE + 8.0)
            if score >= self.SEARCH_MIN_SCORE:
                scored.append(MatchResult(item["code"], item["name"], item.get("unit", ""),
                                          round(min(score, 100.0), 1), item))
        scored.sort(key=lambda r: (-r.score, len(r.name)))
        return scored[:limit]
