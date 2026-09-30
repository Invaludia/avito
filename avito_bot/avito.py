"""Поиск на Авито: ссылка поиска, загрузка страницы, разбор карточек, фильтры."""
from __future__ import annotations

import asyncio
import os
import logging
import random
import re
from dataclasses import dataclass
from urllib.parse import urlencode

from bs4 import BeautifulSoup

from .catalog import Item, Settings

log = logging.getLogger("avito_bot")
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


# Адреса объявлений Питера и Ленобласти. Авито при нехватке объявлений подмешивает
# «объявления в других городах», их отсекаем по этому списку.
SPB_LO_PREFIXES = ("sankt-peterburg", "leningradskaya_oblast")
LO_TOWNS = {
    "gatchina", "vsevolozhsk", "murino", "kudrovo", "sertolovo", "tosno", "kirovsk", "vyborg",
    "kirishi", "luga", "sosnovyy_bor", "tihvin", "tikhvin", "volhov", "volkhov", "kingisepp",
    "priozersk", "otradnoe", "nikolskoe", "kommunar", "sestroretsk", "zelenogorsk", "pushkin",
    "pavlovsk", "petergof", "lomonosov", "kolpino", "krasnoe_selo", "shlisselburg", "yanino-1",
    "bugry", "novoe_devyatkino", "shushary", "siverskiy", "vyritsa", "lebyazhe", "roshchino",
    "sosnovo", "tayczy", "taytsy", "voyskovitsy", "vsevolozhskiy_rayon", "gatchinskiy_rayon",
    "kuzmolovskiy", "sverdlova", "romanovka", "tokskovo", "toksovo", "novosaratovka", "lesnoy",
    "pargolovo", "levashovo", "metallostroy", "ulyanovka", "nikolskoye", "podporozhe", "slantsy",
    "volosovo", "lodeynoe_pole", "boksitogorsk", "pikalevo", "ivangorod", "primorsk", "svetogorsk",
    "kamennogorsk", "vysotsk", "syasstroy", "novaya_ladoga", "lyuban", "kirovskiy_rayon",
}


def in_spb_lo(listing) -> bool:
    r = listing.region
    return r.startswith(SPB_LO_PREFIXES) or r in LO_TOWNS


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

    @property
    def region(self) -> str:
        """Первая часть адреса объявления: /sankt-peterburg/..., /gatchina/..., /moskva/..."""
        path = self.url.split("avito.ru/", 1)[-1]
        return path.split("/", 1)[0].lower()


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
    if not out and _looks_blocked(html):
        raise Blocked("страница с капчей или ограничением доступа")
    return out


def _looks_blocked(html: str) -> bool:
    # Проверяем только когда карточек нет: слово captcha встречается и в скриптах обычной выдачи
    low = html.lower()
    return any(s in low for s in ("доступ ограничен", "firewall-title", "проблема с ip", "geetest", "captcha-container"))


def _has_word(text: str, word: str) -> bool:
    # Слово целиком, чтобы «Ti» не срабатывало внутри «Titan», а «6» внутри «1060»
    return re.search(rf"(?<![\w]){re.escape(word.lower())}(?![\w])", text) is not None


def _norm_mem(text: str) -> str:
    # «16 Гб», «16gb», «16 GB», «16G» -> «16gb», чтобы версии по памяти сравнивались одинаково
    return re.sub(r"(\d+)\s*(?:gb|гб|g|г)(?![\w])", r"\1gb", text.lower())


def accept(listing: Listing, item: Item, settings: Settings) -> bool:
    title = _norm_mem(listing.title)
    full = _norm_mem(listing.text)
    if settings.spb_only and not in_spb_lo(listing):
        return False
    if any(_has_word(title, _norm_mem(w)) for w in item.minus):
        return False
    if any(w.lower() in full for w in settings.stop_words):
        return False
    # Обязательные слова ищем в заголовке и кусочке описания: объём памяти часто пишут только там
    if not all(_has_word(full, _norm_mem(w)) for w in item.must):
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
            headers = {"Accept-Language": "ru-RU,ru;q=0.9"}
            if self._session is None:
                self._session = AsyncSession(impersonate="chrome", timeout=30)
                # Сначала главная страница, как у обычного посетителя: Авито выдаёт cookies
                await self._session.get(BASE + "/", headers=headers)
                await asyncio.sleep(random.uniform(2, 4))
            resp = await self._session.get(url, headers=headers)
            await asyncio.sleep(random.uniform(self.min_delay, self.max_delay))
        log.info("Авито %s -> HTTP %s, %d байт", url, resp.status_code, len(resp.text))
        if resp.status_code in (403, 429):
            self._dump(resp.text)
            await self.reset()
            raise Blocked(f"HTTP {resp.status_code}")
        resp.raise_for_status()
        return resp.text

    def _dump(self, html: str):
        try:
            with open("last_block.html", "w", encoding="utf-8") as f:
                f.write(html)
        except OSError:
            pass

    async def reset(self):
        """Новая сессия (новые cookies) после блокировки."""
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def search(self, item: Item, settings: Settings) -> list[Listing]:
        html = await self.fetch(search_url(item, settings))
        try:
            found = parse_listings(html)
        except Blocked:
            self._dump(html)
            await self.reset()
            raise
        ok = [l for l in found if accept(l, item, settings)]
        other = sorted({l.region for l in found if not in_spb_lo(l)})
        if other and settings.spb_only:
            log.info("%s: отброшены объявления из других мест: %s", item.title, ", ".join(other))
        log.info("%s: карточек %d, подошло %d", item.title, len(found), len(ok))
        return ok

    async def close(self):
        if self._session is not None:
            await self._session.close()


class BrowserClient(AvitoClient):
    """Открывает Авито в настоящем браузере (Edge есть на любом Windows).

    Профиль браузера хранится в папке browser_profile, поэтому cookies живут между запусками.
    Если Авито покажет капчу, её можно решить прямо в окне браузера: бот подождёт.
    """

    CAPTCHA_WAIT = 180  # секунд ждём, пока человек решит капчу в окне

    def __init__(self, channel: str = "msedge", headless: bool = False, executable: str | None = None, **kw):
        super().__init__(**kw)
        self.channel, self.headless, self.executable = channel, headless, executable
        self._pw = self._ctx = self._page = None
        self.on_captcha = None  # async-функция, которую бот вызывает, чтобы написать в Telegram

    async def _open(self):
        from playwright.async_api import async_playwright
        self._pw = await async_playwright().start()
        opts = dict(headless=self.headless, locale="ru-RU", viewport={"width": 1280, "height": 900},
                    args=["--disable-blink-features=AutomationControlled"])
        if self.executable:
            opts["executable_path"] = self.executable
        else:
            opts["channel"] = self.channel
        self._ctx = await self._pw.chromium.launch_persistent_context("browser_profile", **opts)
        self._page = self._ctx.pages[0] if self._ctx.pages else await self._ctx.new_page()

    async def fetch(self, url: str) -> str:
        async with self._lock:
            if self._page is None:
                await self._open()
            resp = await self._page.goto(url, wait_until="domcontentloaded", timeout=60000)
            await self._page.wait_for_timeout(random.randint(1500, 3000))
            status = resp.status if resp else 0
            html = await self._page.content()
            log.info("Авито (браузер) %s -> HTTP %s, %d байт", url, status, len(html))
            if not parse_listings_safe(html) and (status in (403, 429) or _looks_blocked(html)):
                self._dump(html)
                if self.on_captcha:
                    await self.on_captcha()
                # Ждём, пока в окне решат капчу и появятся объявления
                for _ in range(self.CAPTCHA_WAIT // 5):
                    await self._page.wait_for_timeout(5000)
                    html = await self._page.content()
                    if parse_listings_safe(html):
                        log.info("Капча пройдена")
                        break
                else:
                    raise Blocked(f"HTTP {status}, капча не решена")
                if self._page.url.split("?")[0] != url.split("?")[0]:
                    await self._page.goto(url, wait_until="domcontentloaded", timeout=60000)
                    html = await self._page.content()
            await asyncio.sleep(random.uniform(self.min_delay, self.max_delay))
            return html

    async def reset(self):
        pass  # профиль браузера не сбрасываем: cookies после капчи нам и нужны

    async def close(self):
        if self._ctx is not None:
            await self._ctx.close()
        if self._pw is not None:
            await self._pw.stop()


def parse_listings_safe(html: str) -> list[Listing]:
    try:
        return parse_listings(html)
    except Blocked:
        return []


def make_client(mode: str) -> AvitoClient:
    """mode: msedge (по умолчанию), chrome или http (без браузера, быстро, но чаще блокируют)."""
    mode = (mode or "msedge").strip().lower()
    if mode == "http":
        return AvitoClient()
    headless = os.environ.get("HEADLESS", "0") == "1"
    return BrowserClient(channel=mode, headless=headless, executable=os.environ.get("BROWSER_PATH") or None)
