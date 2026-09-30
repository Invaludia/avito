"""Поиск на Авито: ссылка поиска, загрузка страницы, разбор карточек, фильтры."""
from __future__ import annotations

import asyncio
import random
import re
from dataclasses import dataclass
from urllib.parse import urlencode

from bs4 import BeautifulSoup

from .catalog import Item, Settings

BASE = "https://www.avito.ru"
DEFAULT_SECTIONS = {
    "видеокарты": "tovary_dlya_kompyutera/komplektuyuschie/videokarty",
    "процессоры": "tovary_dlya_kompyutera/komplektuyuschie/protsessory",
    "материнки": "tovary_dlya_kompyutera/komplektuyuschie/materinskie_platy",
    "оперативка": "tovary_dlya_kompyutera/komplektuyuschie/operativnaya_pamyat",
}
# Город из таблицы -> (регион в адресе Авито, использовать радиус)
REGIONS = {
    "санкт-петербург + ло (50 км)": ("sankt-peterburg", True),
    "санкт-петербург": ("sankt-peterburg", False),
    "ленинградская область": ("leningradskaya_oblast", False),
    "вся россия": ("rossiya", False),
}


class Blocked(Exception):
    """Авито показал капчу или ограничил доступ."""


@dataclass
class Listing:
    id: str
    title: str
    price: int | None
    url: str
    text: str = ""       # заголовок + кусок описания с карточки, для стоп-слов
    place: str = ""
    date: str = ""


def search_url(item: Item, settings: Settings) -> str:
    city = (item.city or settings.city).strip().lower()
    region, use_radius = REGIONS.get(city, ("sankt-peterburg", True))
    section = settings.avito_section(item.category) or DEFAULT_SECTIONS.get(item.category.lower(), "")
    params = {"q": item.query, "s": "1" if settings.sort_by_price else "104"}
    if item.price_min: params["pmin"] = item.price_min
    if item.price_max: params["pmax"] = item.price_max
    if use_radius and settings.radius_km: params["radius"] = settings.radius_km
    if item.delivery_only or settings.delivery_only: params["d"] = 1
    path = f"{region}/{section}".rstrip("/")
    return f"{BASE}/{path}?{urlencode(params)}"


def _to_price(v) -> int | None:
    if v is None: return None
    d = re.sub(r"\D", "", str(v))
    return int(d) if d else None


def parse_listings(html: str) -> list[Listing]:
    if _looks_blocked(html):
        raise Blocked("капча или ограничение доступа")
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for card in soup.select('[data-marker="item"]'):
        a = card.select_one('[data-marker="item-title"]') or card.select_one('a[itemprop="url"]')
        if not a or not a.get("href"):
            continue
        title_el = card.select_one('[itemprop="name"]')
        title = (a.get("title") or (title_el.get_text(" ", strip=True) if title_el else a.get_text(" ", strip=True))).strip()
        price_el = card.select_one('meta[itemprop="price"]')
        price = _to_price(price_el.get("content")) if price_el else None
        if price is None:
            p = card.select_one('[data-marker="item-price"]')
            price = _to_price(p.get_text()) if p else None
        href = a["href"]
        url = href if href.startswith("http") else BASE + href.split("?")[0]
        place = card.select_one('[data-marker="item-address"]')
        date = card.select_one('[data-marker="item-date"]')
        out.append(Listing(
            id=card.get("data-item-id") or url.rsplit("_", 1)[-1],
            title=title, price=price, url=url,
            text=card.get_text(" ", strip=True),
            place=place.get_text(" ", strip=True) if place else "",
            date=date.get_text(" ", strip=True) if date else "",
        ))
    return out


def _looks_blocked(html: str) -> bool:
    low = html[:20000].lower()
    return any(s in low for s in ("доступ ограничен", "captcha", "firewall-title", "проблема с ip"))


def _has_word(text: str, word: str) -> bool:
    # Слово целиком, чтобы «Ti» не срабатывало внутри «Titan», а «6» внутри «1060»
    return re.search(rf"(?<![\w]){re.escape(word.lower())}(?![\w])", text) is not None


def accept(listing: Listing, item: Item, settings: Settings) -> bool:
    title = listing.title.lower()
    full = listing.text.lower()
    if any(_has_word(title, w) for w in item.minus):
        return False
    if any(w.lower() in full for w in settings.stop_words):
        return False
    if not all(_has_word(title, w) for w in item.must):
        return False
    # Номер модели из запроса (4060, 12400F, 5800X3D) должен быть в заголовке:
    # поиск Авито нечёткий и подмешивает соседние модели
    num = next((w for w in re.findall(r"\w+", item.query.lower()) if sum(c.isdigit() for c in w) >= 4), None)
    if num and num not in title.replace(" ", ""):
        return False
    if listing.price is not None:
        if item.price_min and listing.price < item.price_min: return False
        if item.price_max and listing.price > item.price_max: return False
    return True


class AvitoClient:
    """Медленный клиент: пауза между запросами, один запрос за раз."""

    def __init__(self, min_delay: float = 6.0, max_delay: float = 12.0):
        self.min_delay, self.max_delay = min_delay, max_delay
        self._lock = asyncio.Lock()
        self._session = None

    async def fetch(self, url: str) -> str:
        from curl_cffi.requests import AsyncSession
        async with self._lock:
            if self._session is None:
                self._session = AsyncSession(impersonate="chrome", timeout=30)
            resp = await self._session.get(url, headers={"Accept-Language": "ru-RU,ru;q=0.9"})
            await asyncio.sleep(random.uniform(self.min_delay, self.max_delay))
        if resp.status_code in (403, 429):
            raise Blocked(f"HTTP {resp.status_code}")
        resp.raise_for_status()
        return resp.text

    async def search(self, item: Item, settings: Settings) -> list[Listing]:
        html = await self.fetch(search_url(item, settings))
        return [l for l in parse_listings(html) if accept(l, item, settings)]

    async def close(self):
        if self._session is not None:
            await self._session.close()
