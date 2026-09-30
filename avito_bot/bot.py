"""Telegram-бот: дерево кнопок из таблицы, объявления, уведомления о новых."""
from __future__ import annotations

import asyncio
import html
import logging
import os
import time

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from dotenv import load_dotenv

from . import catalog
from .avito import Blocked, make_client
from .db import DB

log = logging.getLogger("avito_bot")


class App:
    def __init__(self):
        load_dotenv()
        self.source = os.environ.get("CATALOG", "avito_catalog.xlsx")
        self.allowed = {int(x) for x in os.environ.get("ALLOWED_USERS", "").replace(" ", "").split(",") if x}
        self.db = DB(os.environ.get("DB_PATH", "avito.sqlite3"))
        self.client = make_client(os.environ.get("BROWSER", "msedge"))
        self.bot = Bot(os.environ["BOT_TOKEN"])
        self.dp = Dispatcher()
        self.reload()
        self._register()

    def reload(self):
        try:
            items, self.settings = catalog.load(self.source)
        except Exception as e:
            if hasattr(self, "tree"):
                raise
            # Первый запуск: без таблицы бот бесполезен, берём шаблон из папки
            log.error("Не удалось скачать таблицу (%s). Беру avito_catalog.xlsx из папки.", e)
            items, self.settings = catalog.load("avito_catalog.xlsx")
        self.tree = catalog.Tree(items)
        log.info("каталог: %d моделей", len(items))

    def ok(self, user_id: int) -> bool:
        return not self.allowed or user_id in self.allowed

    # ---------- клавиатуры ----------
    def menu(self, node: catalog.Node) -> tuple[str, InlineKeyboardMarkup]:
        rows, row = [], []
        for child in node.children.values():
            row.append(InlineKeyboardButton(text=child.title, callback_data=f"n:{child.id}"))
            if len(row) == (2 if len(node.children) > 4 else 1):
                rows.append(row); row = []
        if row: rows.append(row)
        if node.items and node.children:
            rows.insert(0, [InlineKeyboardButton(text=f"Объявления: {node.title}", callback_data=f"i:{node.id}")])
        if node.parent:
            rows.append([InlineKeyboardButton(text="← Назад", callback_data=f"n:{node.parent.id}"),
                         InlineKeyboardButton(text="В начало", callback_data="n:root")])
        title = " → ".join(node.path) or "Что ищем?"
        return html.escape(title), InlineKeyboardMarkup(inline_keyboard=rows)

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
            rows += [(r, it.variant) for r in self.db.top(it.key, s.show_count, s.sort_by_price)]
        if s.sort_by_price:
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
            [InlineKeyboardButton(text="← Назад", callback_data=f"n:{node.parent.id}"),
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
                        await self.check(it, notify=False)
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
    async def check(self, item: catalog.Item, notify: bool = True):
        found = await self.client.search(item, self.settings)
        new = self.db.save(item.key, found)
        if notify and new and self.settings.notify:
            for l in new:
                ps = f"{l.price:,} ₽".replace(",", " ") if l.price else "цена не указана"
                text = (f"🆕 <b>{html.escape(item.title)}</b>\n<a href=\"{html.escape(l.url)}\">"
                        f"{html.escape(l.title)}</a> — <b>{ps}</b>")
                for uid in self.allowed:
                    await self.bot.send_message(uid, text, parse_mode="HTML")

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
