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


def search_url(item: Item, settings: Settings, market: bool = False) -> str:
    """market=True — для обзора рынка: без ценового диапазона, свежие объявления первыми."""
    city = (item.city or settings.city).strip().lower()
    region, use_radius = REGIONS.get(city, ("sankt-peterburg", True))
    section = settings.avito_section(item.category) or DEFAULT_SECTIONS.get(item.category.lower(), "")
    params = {"q": item.query, "s": "104" if market or not settings.sort_by_price else "1"}
    if item.price_min and not market: params["pmin"] = item.price_min
    if item.price_max and not market: params["pmax"] = item.price_max
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
    """Минус-слово: отдельное слово или суффикс номера модели.
    «Ti» ловит «RTX 4060 Ti» и «4060Ti», но не «Titan»; «KF» ловит «12600KF»."""
    return re.search(rf"(?<![a-zа-яё]){re.escape(word.lower())}(?![a-zа-яё0-9])", text) is not None


def _starts_word(text: str, word: str) -> bool:
    """Обязательное слово: начало слова. «12400» есть в «12400F», «8gb» не находится в «18gb»."""
    return re.search(rf"(?<![a-zа-яё0-9]){re.escape(word.lower())}", text) is not None


# Стоп-слово не считается, если перед ним отрицание: «без артефактов», «нет сколов», «не было ремонта»
_NEGATION = re.compile(r"(?:без|нет|не было|никаких|никогда не было|отсутствуют|ни одного|ни разу не)\s+(?:\w+\s+){0,2}$")
# «проблем нет», «артефактов не было», «ошибок не замечено»
_NEGATION_AFTER = re.compile(r"^\w*[\s,]+(?:\w+\s+)?(?:нет|не было|не замечено|не наблюдается|не наблюдалось|отсутствуют)\b")
_FALSE_FRIENDS = {"скол": ("сколько", "скольк")}


def _has_stop(text: str, word: str) -> bool:
    w = word.lower().strip()
    if not w:
        return False
    for m in re.finditer(rf"(?<![a-zа-яё0-9]){re.escape(w)}", text):
        rest = text[m.start():m.start() + len(w) + 4]
        if any(rest.startswith(f) for f in _FALSE_FRIENDS.get(w, ())):
            continue
        before = text[max(0, m.start() - 40):m.start()]
        if _NEGATION.search(before) or re.search(r"(?<![а-яё])не\s+$", before):  # «не греется», «не глючит»
            continue
        if _NEGATION_AFTER.search(text[m.start() + len(w):m.start() + len(w) + 40]):
            continue
        return True
    return False


def _norm_mem(text: str) -> str:
    # «16 Гб», «16gb», «16 GB», «16G» -> «16gb», чтобы версии по памяти сравнивались одинаково
    return re.sub(r"(\d+)\s*(?:gb|гб|g|г)(?![\w])", r"\1gb", text.lower())


def accept(listing: Listing, item: Item, settings: Settings, check_price: bool = True) -> bool:
    title = _norm_mem(listing.title)
    full = _norm_mem(listing.title + " " + listing.text)
    if settings.spb_only and not in_spb_lo(listing):
        return False
    if any(_has_word(title, _norm_mem(w)) for w in item.minus):
        return False
    if any(_has_stop(full, w) for w in settings.stop_words):
        return False
    # Коробки, кабели, «куплю» и т.п. отсекаем только по заголовку: «в коробке» в описании — это нормально
    if any(_has_stop(title, w) for w in settings.title_stop_words):
        return False
    # Обязательные слова ищем в заголовке и кусочке описания: объём памяти часто пишут только там
    if not all(_starts_word(full, _norm_mem(w)) for w in item.must):
        return False
    # Номер модели из запроса (4060, 12400F, 5800X3D) должен быть в заголовке:
    # поиск Авито нечёткий и подмешивает соседние модели
    num = next((w for w in re.findall(r"\w+", item.query.lower()) if sum(c.isdigit() for c in w) >= 4), None)
    if num and num not in title.replace(" ", ""):
        return False
    if check_price and listing.price is not None:
        if item.price_min and listing.price < item.price_min: return False
        if item.price_max and listing.price > item.price_max: return False
    return True


# Статус в шапке страницы (там же бывает «Продано N товаров» у продавца, поэтому «продано» ищем только в описании)
RESERVED_HEAD = ["зарезервирован", "в резерве", "забронирован", "снято с публикации", "объявление снято"]
RESERVED_DESC = RESERVED_HEAD + ["бронь", "продано", "продана", "товар продан"]


def check_detail(html: str, settings: Settings) -> tuple[bool, str, str]:
    """Страница объявления: бронь / продано по шапке страницы, дефекты — по полному описанию."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    parts = []
    for sel in ('[data-marker="item-view/item-description"]', '[itemprop="description"]'):
        el = soup.select_one(sel)
        if el:
            parts.append(el.get_text(" ", strip=True))
    # Запасной вариант, если Авито поменяет вёрстку: описание есть в мета-тегах страницы
    for attrs in ({"name": "description"}, {"property": "og:description"}):
        el = soup.find("meta", attrs=attrs)
        if el and el.get("content"):
            parts.append(el["content"])
    desc = _norm_mem(" ".join(parts))
    # Шапка страницы (заголовок, цена, статус) — до блока «похожие объявления»
    head = soup.get_text(" ", strip=True).lower()[:6000]
    for w in RESERVED_HEAD:
        if _has_stop(head, w):
            return False, w, desc
    for w in RESERVED_DESC:
        if _has_stop(desc, w):
            return False, w, desc
    for w in settings.stop_words:
        if _has_stop(desc, w):
            return False, w, desc
    return True, "", desc


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

    async def search(self, item: Item, settings: Settings, market: bool = False) -> list[Listing]:
        html = await self.fetch(search_url(item, settings, market))
        try:
            found = parse_listings(html)
        except Blocked:
            self._dump(html)
            await self.reset()
            raise
        ok = [l for l in found if accept(l, item, settings, check_price=not market)]
        other = sorted({l.region for l in found if not in_spb_lo(l)})
        if other and settings.spb_only:
            log.info("%s: отброшены объявления из других мест: %s", item.title, ", ".join(other))
        log.info("%s: карточек %d, подошло %d", item.title, len(found), len(ok))
        return ok

    async def details(self, listing: Listing, settings: Settings) -> tuple[bool, str, str]:
        html = await self.fetch(listing.url)
        ok, reason, desc = check_detail(html, settings)
        log.info("Страница %s: %s", listing.url, "ок" if ok else f"отброшено ({reason})")
        return ok, reason, desc

    async def close(self):
        if self._session is not None:
            await self._session.close()


class BrowserClient(AvitoClient):
    """Открывает Авито в настоящем браузере: Chrome, а если его нет, Edge.

    Профиль браузера хранится в папке browser_profile, поэтому cookies живут между запусками.
    Если Авито покажет капчу, её можно решить прямо в окне браузера: бот подождёт.
    """

    CAPTCHA_WAIT = 180  # секунд ждём, пока человек решит капчу в окне

    def __init__(self, channel: str = "chrome", headless: bool = False, executable: str | None = None, **kw):
        super().__init__(**kw)
        self.channel, self.headless, self.executable = channel, headless, executable
        self._pw = self._ctx = self._page = None
        self.warmup = True
        self.on_captcha = None  # async-функция, которую бот вызывает, чтобы написать в Telegram

    def _find_browser(self) -> tuple[str, str] | None:
        """Путь к установленному Chrome или Edge."""
        if self.executable:
            return self.executable, "custom"
        env = os.environ
        places = {
            "chrome": [
                os.path.join(env.get("ProgramFiles", r"C:\Program Files"), r"Google\Chrome\Application\chrome.exe"),
                os.path.join(env.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), r"Google\Chrome\Application\chrome.exe"),
                os.path.join(env.get("LOCALAPPDATA", ""), r"Google\Chrome\Application\chrome.exe"),
                "/usr/bin/google-chrome", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            ],
            "msedge": [
                os.path.join(env.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), r"Microsoft\Edge\Application\msedge.exe"),
                os.path.join(env.get("ProgramFiles", r"C:\Program Files"), r"Microsoft\Edge\Application\msedge.exe"),
                "/usr/bin/microsoft-edge",
            ],
        }
        for ch in dict.fromkeys([self.channel, "chrome", "msedge"]):
            for path in places.get(ch, []):
                if path and os.path.isfile(path):
                    return path, ch
        return None

    async def _open(self):
        """Запускаем браузер сами, как обычный человек, и подключаемся к нему.

        Если запускать через Playwright напрямую, браузер получает служебные флаги
        (--enable-automation, --no-sandbox), и Авито видит, что им управляет программа.
        """
        import socket
        import subprocess
        from playwright.async_api import async_playwright

        found = self._find_browser()
        if not found:
            raise RuntimeError("Не нашёл Google Chrome или Microsoft Edge на компьютере")
        exe, name = found
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        # Отдельный профиль в папке бота: твой обычный Chrome, вкладки и вход в аккаунты не трогаются
        profile = os.path.abspath(f"browser_profile_{name}")
        args = [exe, f"--remote-debugging-port={port}", f"--user-data-dir={profile}",
                "--no-first-run", "--no-default-browser-check", "--lang=ru-RU", "--window-size=1280,900"]
        if self.headless:
            args.append("--headless=new")
        args.append("about:blank")
        self._proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        log.info("Открываю Авито в браузере: %s", exe)

        self._pw = await async_playwright().start()
        last = None
        for _ in range(40):  # до 20 секунд ждём, пока браузер запустится
            try:
                self._browser = await self._pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
                break
            except Exception as e:
                last = e
                await asyncio.sleep(0.5)
        else:
            raise RuntimeError(f"Браузер не ответил: {last}")
        self._ctx = self._browser.contexts[0] if self._browser.contexts else await self._browser.new_context()
        self._page = self._ctx.pages[0] if self._ctx.pages else await self._ctx.new_page()
        self._browser.on("disconnected", lambda _: setattr(self, "_page", None))
        # Сначала главная, как обычный посетитель
        if not self.warmup:
            return
        try:
            await self._page.goto(BASE + "/", wait_until="domcontentloaded", timeout=60000)
            await self._page.wait_for_timeout(random.randint(3000, 5000))
        except Exception as e:
            log.warning("Главная Авито не открылась: %s", str(e).splitlines()[0])
            await asyncio.sleep(2)

    async def _goto(self, url: str):
        """Открывает страницу; если окно браузера закрыли, запускает браузер заново."""
        for attempt in (1, 2):
            if self._page is None or self._page.is_closed():
                await self._shutdown()
                await self._open()
            try:
                return await self._page.goto(url, wait_until="domcontentloaded", timeout=60000)
            except Exception as e:
                if attempt == 2 or "closed" not in str(e).lower():
                    raise
                log.warning("Окно браузера закрыто, открываю заново")
                self._page = None

    async def _shutdown(self):
        for obj, meth in ((getattr(self, "_browser", None), "close"), (self._pw, "stop")):
            if obj is not None:
                try:
                    await getattr(obj, meth)()
                except Exception:
                    pass
        proc = getattr(self, "_proc", None)
        if proc is not None and proc.poll() is None:
            proc.terminate()
        self._pw = self._ctx = self._page = self._browser = self._proc = None

    async def fetch(self, url: str) -> str:
        async with self._lock:
            resp = await self._goto(url)
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
                    await self._goto(url)
                    html = await self._page.content()
            await asyncio.sleep(random.uniform(self.min_delay, self.max_delay))
            return html

    async def reset(self):
        pass  # профиль браузера не сбрасываем: cookies после капчи нам и нужны

    async def close(self):
        await self._shutdown()


def parse_listings_safe(html: str) -> list[Listing]:
    try:
        return parse_listings(html)
    except Blocked:
        return []


def make_client(mode: str) -> AvitoClient:
    """mode: chrome (по умолчанию), msedge или http (без браузера, быстро, но чаще блокируют)."""
    mode = (mode or "chrome").strip().lower()
    if mode == "http":
        return AvitoClient()
    headless = os.environ.get("HEADLESS", "0") == "1"
    return BrowserClient(channel=mode, headless=headless, executable=os.environ.get("BROWSER_PATH") or None)
