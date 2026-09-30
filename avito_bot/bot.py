"""Telegram-бот: дерево кнопок из таблицы, объявления, уведомления о новых."""
from __future__ import annotations

import asyncio
import statistics
import html
import logging
import os
import time

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from dotenv import load_dotenv

from . import catalog
from .avito import Blocked, accept, make_client, parse_listings, search_url
from .db import DB

log = logging.getLogger("avito_bot")


class App:
    def __init__(self):
        load_dotenv()
        self.source = os.environ.get("CATALOG", "avito_catalog.xlsx")
        self.allowed = {int(x) for x in os.environ.get("ALLOWED_USERS", "").replace(" ", "").split(",") if x}
        self.db = DB(os.environ.get("DB_PATH", "avito.sqlite3"))
        self.client = make_client(os.environ.get("BROWSER", "chrome"))
        self.bot = Bot(os.environ["BOT_TOKEN"])
        self.dp = Dispatcher()
        self.reload()
        self._register()

    def reload(self):
        try:
            items, self.settings = catalog.load(self.source)
            self.fallback = False
        except Exception as e:
            if hasattr(self, "tree"):
                raise
            self.fallback = True
            # Первый запуск: без таблицы бот бесполезен, берём шаблон из папки
            log.error("Не удалось скачать таблицу (%s). Беру avito_catalog.xlsx из папки.", e)
            items, self.settings = catalog.load("avito_catalog.xlsx")
        self.tree = catalog.Tree(items)
        log.info("каталог: %d моделей, таблица: %s", len(items), "шаблон из папки" if self.fallback else self.source)

    def ok(self, user_id: int) -> bool:
        return not self.allowed or user_id in self.allowed

    # ---------- клавиатуры ----------
    @staticmethod
    def _down(node: catalog.Node) -> catalog.Node:
        """Уровни с единственной кнопкой пропускаем, чтобы не нажимать лишнее."""
        while len(node.children) == 1 and not node.items:
            node = next(iter(node.children.values()))
        return node

    def _up(self, node: catalog.Node) -> catalog.Node | None:
        """Куда ведёт «Назад»: ближайший уровень, где есть выбор."""
        p = node.parent
        while p is not None and self._down(p) is node:
            p = p.parent
        return self._down(p) if p is not None else None

    def menu(self, node: catalog.Node) -> tuple[str, InlineKeyboardMarkup]:
        node = self._down(node)
        rows, row = [], []
        for child in node.children.values():
            row.append(InlineKeyboardButton(text=child.title, callback_data=f"n:{child.id}"))
            if len(row) == (2 if len(node.children) > 4 else 1):
                rows.append(row); row = []
        if row: rows.append(row)
        if node.items and node.children:
            rows.insert(0, [InlineKeyboardButton(text=f"Объявления: {node.title}", callback_data=f"i:{node.id}")])
        up = self._up(node)
        if up is not None:
            rows.append([InlineKeyboardButton(text="← Назад", callback_data=f"n:{up.id}"),
                         InlineKeyboardButton(text="В начало", callback_data="n:root")])
        title = html.escape(" → ".join(node.path) or "Что ищем?")
        if getattr(self, "fallback", False):
            title = "⚠️ Google Таблица не скачалась, работаю по шаблону из папки. Проверь /table\n\n" + title
        return title, InlineKeyboardMarkup(inline_keyboard=rows)

    def listings_view(self, node: catalog.Node) -> tuple[str, InlineKeyboardMarkup]:
        s = self.settings
        fmt = lambda v: f"{v:,}".replace(",", " ")
        head = f"<b>{html.escape(' → '.join(node.path))}</b>\n"
        for it in node.items:
            if it.price_min or it.price_max:
                label = f"{html.escape(it.variant)}: " if it.variant else "Цена: "
                head += f"{label}{fmt(it.price_min) if it.price_min else 0} – {fmt(it.price_max) if it.price_max else '∞'} ₽\n"
        rows = []
        for it in node.items:
            # Фильтры применяем и при показе: после правок таблицы старые объявления из базы тоже отсекаются
            rows += [((l.title, l.price, l.url, l.place), it.variant)
                     for l in self.db.rows(it.key) if accept(l, it, s)]
        rows.sort(key=lambda x: (x[0][1] is None, x[0][1] or 0))
        rows = rows[:s.show_count]
        checked = [c for c in (self.db.checked_at(it.key) for it in node.items) if c]
        if not checked:
            body = "\nЕщё не проверял. Нажми «Обновить»."
        elif not rows:
            body = "\nПодходящих объявлений сейчас нет."
        else:
            lines = []
            for i, ((title, p, url, place), variant) in enumerate(rows, 1):
                ps = f"{fmt(p)} ₽" if p else "цена не указана"
                tag = f" [{html.escape(variant)}]" if variant else ""
                lines.append(f'{i}. <a href="{html.escape(url)}">{html.escape(title)}</a>{tag} — <b>{ps}</b>'
                             + (f"\n    {html.escape(place)}" if place else ""))
            body = "\n" + "\n".join(lines)
        if checked:
            body += f"\n\n<i>Проверено {int((time.time() - min(checked)) // 60)} мин назад</i>"
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить", callback_data=f"r:{node.id}")],
            [InlineKeyboardButton(text="← Назад", callback_data=f"n:{(self._up(node) or self.tree.root).id}"),
             InlineKeyboardButton(text="В начало", callback_data="n:root")],
        ])
        return head + body, kb

    # ---------- обработчики ----------
    def _register(self):
        dp = self.dp

        @dp.message(CommandStart())
        async def start(m: Message):
            if not self.ok(m.from_user.id):
                return await m.answer(f"Нет доступа. Твой id: {m.from_user.id}")
            text, kb = self.menu(self.tree.root)
            await m.answer(text, reply_markup=kb, parse_mode="HTML")

        @dp.message(Command("reload"))
        async def reload_cmd(m: Message):
            if not self.ok(m.from_user.id): return
            await asyncio.to_thread(self.reload)
            await m.answer(f"Таблица перечитана, моделей: {len(self.tree.items)}")

        @dp.message(Command("scan"))
        async def scan_cmd(m: Message):
            if not self.ok(m.from_user.id): return
            items = list(self.tree.items.values())
            await m.answer(f"Сканирую рынок: {len(items)} позиций, это займёт пару минут…")
            try:
                text = await self.scan(items)
            except Blocked as e:
                text = f"Авито ограничил запросы ({e}). Попробуй позже или пройди проверку в окне Chrome."
            for part in _chunks(text):
                await m.answer(part, parse_mode="HTML", disable_web_page_preview=True)

        @dp.message(Command("table"))
        async def table_cmd(m: Message):
            if not self.ok(m.from_user.id): return
            parts = (m.text or "").split(maxsplit=1)
            if len(parts) < 2 or "docs.google.com/spreadsheets" not in parts[1]:
                return await m.answer(f"Сейчас таблица: {self.source}\nЧтобы сменить: /table ссылка_на_google_таблицу")
            url = parts[1].strip()
            try:
                items, _ = await asyncio.to_thread(catalog.load, url)
            except Exception as e:
                return await m.answer(f"Не смог прочитать эту таблицу: {e}\nПроверь доступ «Все, у кого есть ссылка → Читатель».")
            _set_env("CATALOG", url)
            self.source = url
            await asyncio.to_thread(self.reload)
            await m.answer(f"Таблица подключена, моделей: {len(items)}. Нажми /start.")

        @dp.callback_query(F.data.startswith(("n:", "i:", "r:")))
        async def nav(c: CallbackQuery):
            if not self.ok(c.from_user.id):
                return await c.answer("Нет доступа")
            kind, nid = c.data.split(":", 1)
            node = self.tree.get(nid)
            if node is None:
                await c.answer("Таблица изменилась, начни заново")
                node = self.tree.root; kind = "n"
            if kind == "r" and node.items:
                await c.answer("Ищу на Авито…")
                try:
                    for it in node.items:
                        await self.check(it, notify=False, detail_limit=4)
                except Blocked as e:
                    await c.message.answer(f"Авито временно ограничил запросы ({e}). Если на компьютере открыто окно браузера с капчей, реши её и нажми «Обновить» ещё раз.")
            elif kind == "n" and not node.children and node.items:
                kind = "i"
            if kind in ("i", "r") and node.items:
                text, kb = self.listings_view(node)
            else:
                text, kb = self.menu(node)
            await c.message.edit_text(text, reply_markup=kb, parse_mode="HTML", disable_web_page_preview=True)
            await c.answer()

    # ---------- парсинг ----------
    async def check(self, item: catalog.Item, notify: bool = True, detail_limit: int = 8):
        found = await self.client.search(item, self.settings)
        # Страницу каждого объявления открываем один раз: бронь, «продано», дефекты в полном описании.
        # За раз проверяем не больше detail_limit, остальные — в следующий проход
        todo = [l for l in found if self.db.detail(l.id) is None][:detail_limit]
        for l in todo:
            try:
                ok, reason = await self.client.details(l, self.settings)
            except Blocked:
                raise
            except Exception as e:
                log.warning("Не открылась страница %s: %s", l.url, e)
                continue
            self.db.set_detail(l.id, ok, reason)
        found = [l for l in found if self.db.detail(l.id) != 0]
        new = self.db.save(item.key, found)
        new = [l for l in new if self.db.detail(l.id) == 1]  # уведомляем только о проверенных
        if notify and new and self.settings.notify:
            for l in new:
                ps = f"{l.price:,} ₽".replace(",", " ") if l.price else "цена не указана"
                text = (f"🆕 <b>{html.escape(item.title)}</b>\n<a href=\"{html.escape(l.url)}\">"
                        f"{html.escape(l.title)}</a> — <b>{ps}</b>")
                for uid in self.allowed:
                    await self.bot.send_message(uid, text, parse_mode="HTML")

    async def scan(self, items: list[catalog.Item]) -> str:
        """Обзор рынка: сколько объявлений и по каким ценам, без учёта ценового диапазона из таблицы."""
        fmt = lambda v: f"{v:,}".replace(",", " ")
        lines = ["<b>Рынок, Питер + ЛО, только исправные</b>", ""]
        pages: dict[str, list] = {}  # 3060 12GB и 8GB ищутся одним запросом — не ходим на Авито дважды
        for it in items:
            url = search_url(it, self.settings, market=True)
            if url not in pages:
                pages[url] = parse_listings(await self.client.fetch(url))
            found = [l for l in pages[url] if accept(l, it, self.settings, check_price=False)]
            prices = sorted(l.price for l in found if l.price)
            if not prices:
                lines.append(f"<b>{html.escape(it.title)}</b>: объявлений нет\n")
                continue
            med = int(statistics.median(prices))
            in_range = [l for l in found if l.price and (not it.price_min or l.price >= it.price_min)
                        and (not it.price_max or l.price <= it.price_max)]
            cheap = min((l for l in found if l.price), key=lambda l: l.price)
            rng = f"{fmt(it.price_min or 0)}–{fmt(it.price_max) if it.price_max else '∞'}"
            lines.append(f"<b>{html.escape(it.title)}</b>: {len(prices)} шт., "
                         f"от {fmt(prices[0])} до {fmt(prices[-1])} ₽, медиана <b>{fmt(med)} ₽</b>\n"
                         f"   в твоём диапазоне {rng}: {len(in_range)} шт.\n"
                         f'   самая дешёвая: <a href="{html.escape(cheap.url)}">{html.escape(cheap.title[:60])}</a>\n')
        lines.append("<i>Первая страница выдачи Авито (свежие), без проверки брони на странице объявления.</i>")
        return "\n".join(lines)

    async def loop(self):
        await asyncio.sleep(120)  # не начинать обход сразу при запуске
        while True:
            try:
                await asyncio.to_thread(self.reload)
            except Exception:
                log.exception("не удалось прочитать таблицу, работаю со старой")
            for item in list(self.tree.items.values()):
                try:
                    await self.check(item)
                except Blocked as e:
                    log.warning("Авито ограничил доступ (%s), пауза 30 минут", e)
                    for uid in self.allowed:
                        await self.bot.send_message(uid, f"⚠️ Авито ограничил запросы ({e}), делаю паузу 30 минут.")
                    await asyncio.sleep(1800)
                    break
                except Exception:
                    log.exception("ошибка при проверке %s", item.title)
            await asyncio.sleep(self.settings.interval_min * 60)

    async def captcha_alert(self):
        for uid in self.allowed:
            await self.bot.send_message(uid, "🧩 Авито просит капчу. Реши её в окне браузера на компьютере, я подожду 3 минуты.")

    async def run(self):
        if hasattr(self.client, "on_captcha"):
            self.client.on_captcha = self.captcha_alert
        me = await self.bot.get_me()
        print(f"\n=== Бот @{me.username} запущен, напиши ему /start в Telegram. Не закрывай это окно. ===\n", flush=True)
        task = asyncio.create_task(self.loop())
        try:
            await self.dp.start_polling(self.bot)
        finally:
            task.cancel()
            await self.client.close()


def _chunks(text: str, limit: int = 3800) -> list[str]:
    parts, cur = [], ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > limit:
            parts.append(cur); cur = ""
        cur += line + "\n"
    return parts + [cur] if cur.strip() else parts


def _set_env(key: str, value: str, path: str = ".env"):
    """Меняет одну строку в .env, остальное оставляет как есть."""
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except FileNotFoundError:
        lines = []
    lines = [l for l in lines if not l.strip().startswith(f"{key}=")] + [f"{key}={value}"]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        asyncio.run(App().run())
    except KeyError:
        print("Нет BOT_TOKEN в файле .env. Удали .env и запусти run.bat заново.")
    except Exception as e:
        if "Unauthorized" in repr(e) or "token" in repr(e).lower():
            print("Telegram не принял токен. Удали файл .env и запусти run.bat заново, вставив токен целиком.")
        raise


if __name__ == "__main__":
    main()
