"""Співставлення довільного найменування роботи з позицією довідника розцінок."""
from __future__ import annotations

from dataclasses import dataclass

from rapidfuzz import fuzz, process

from .normalize import normalize_name, normalize_unit, stem, stem_phrase


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
    """Fuzzy-пошук по довіднику: точний ключ → збіг зі стемінгом → часткова схожість."""

    # Нижче цього порогу вважаємо, що розцінку не знайдено.
    MIN_SCORE = 62.0
    # Збіг за одиницею виміру дає бонус, розбіжність — штраф.
    UNIT_BONUS = 6.0
    UNIT_PENALTY = 14.0
    # Поріг для пошуку в інтерфейсі (нижче — вважаємо збіг випадковим).
    SEARCH_MIN_SCORE = 55.0

    def __init__(self, items: list[dict]):
        self.items = items
        self._exact: dict[str, dict] = {}
        self._choices: list[str] = []
        self._index: list[dict] = []
        self._tokens: list[set[str]] = []
        self._cat_tokens: list[set[str]] = []
        for item in items:
            key = work_key(item["name"], item.get("unit", ""))
            self._exact.setdefault(key, item)
            self._exact.setdefault(stem_phrase(item["name"]), item)
            self._choices.append(stem_phrase(item["name"]))
            self._index.append(item)
            self._tokens.append(set(normalize_name(item["name"]).split()))
            self._cat_tokens.append(set(normalize_name(item.get("category", "")).split()))

    def match(self, name: str, unit: str = "", limit: int = 1) -> list[MatchResult]:
        if not name or not name.strip():
            return []
        unit_n = normalize_unit(unit)
        key = work_key(name, unit_n)
        hit = self._exact.get(key) or self._exact.get(stem_phrase(name))
        if hit is not None and (not unit_n or normalize_unit(hit.get("unit", "")) == unit_n):
            return [MatchResult(hit["code"], hit["name"], hit.get("unit", ""), 100.0, hit)]

        query = stem_phrase(name)
        if not query:
            return []
        raw = process.extract(query, self._choices, scorer=fuzz.token_set_ratio,
                              limit=max(limit * 4, 12))
        scored: list[MatchResult] = []
        for _choice, score, idx in raw:
            item = self._index[idx]
            item_unit = normalize_unit(item.get("unit", ""))
            adj = float(score)
            if unit_n and item_unit:
                adj += self.UNIT_BONUS if unit_n == item_unit else -self.UNIT_PENALTY
            # Схожість «як є» страхує випадки, де стемінг зрізав забагато.
            adj = max(adj, fuzz.token_set_ratio(normalize_name(name),
                                                normalize_name(item["name"])) - 4)
            scored.append(MatchResult(item["code"], item["name"], item.get("unit", ""),
                                      round(min(adj, 99.9), 1), item))
        scored.sort(key=lambda r: r.score, reverse=True)
        return [s for s in scored if s.score >= self.MIN_SCORE][:limit]

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
            if word.startswith(query_token) or query_token.startswith(word):
                return True
            if len(query_token) >= 4 and len(word) >= 4 and fuzz.ratio(query_token, word) >= 82:
                return True
        return False

    def search(self, text: str, limit: int = 20) -> list[MatchResult]:
        """Пошук для автопідказки: коротке слово має знаходити довгі назви розцінок."""
        text = (text or "").strip()
        if not text:
            return [MatchResult(i["code"], i["name"], i.get("unit", ""), 0.0, i)
                    for i in self.items[:limit]]

        tokens = [stem(t) for t in normalize_name(text).split() if t]
        query = " ".join(tokens) or normalize_name(text)
        scored: list[MatchResult] = []
        for idx, item in enumerate(self._index):
            hits = sum(1 for t in tokens if self._token_hit(t, self._tokens[idx]))
            cat_hits = sum(1 for t in tokens if self._token_hit(t, self._cat_tokens[idx]))
            score = float(fuzz.WRatio(query, self._choices[idx]))
            if tokens and hits == len(tokens):
                # Усі слова запиту знайдені в найменуванні — це точно релевантно.
                score = max(score, 90.0 + min(len(tokens), 5))
            elif hits:
                score = max(score, 58.0 + 9.0 * hits)
            if tokens and cat_hits:
                # Збіг лише за розділом довідника — слабший сигнал, ніж за назвою.
                score = max(score, 62.0 + 4.0 * cat_hits)
            if score >= self.SEARCH_MIN_SCORE:
                scored.append(MatchResult(item["code"], item["name"], item.get("unit", ""),
                                          round(min(score, 100.0), 1), item))
        scored.sort(key=lambda r: (-r.score, len(r.name)))
        return scored[:limit]
