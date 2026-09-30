"""SQLite: найденные объявления по каждой модели."""
from __future__ import annotations

import sqlite3
import time

from .avito import Listing


class DB:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path)
        self.conn.execute("""CREATE TABLE IF NOT EXISTS listings (
            item_key TEXT, id TEXT, title TEXT, price INTEGER, url TEXT, place TEXT, date TEXT,
            first_seen REAL, last_seen REAL, PRIMARY KEY (item_key, id))""")
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(listings)")}
        if "text" not in cols:  # база от старой версии
            self.conn.execute("ALTER TABLE listings ADD COLUMN text TEXT DEFAULT ''")
        self.conn.execute("CREATE TABLE IF NOT EXISTS checked (item_key TEXT PRIMARY KEY, at REAL)")
        # Результат проверки страницы объявления: ok=1 годится, ok=0 бронь/продано/дефект в описании
        self.conn.execute("CREATE TABLE IF NOT EXISTS details (id TEXT PRIMARY KEY, ok INTEGER, reason TEXT, at REAL)")
        self.conn.commit()

    def save(self, item_key: str, listings: list[Listing]) -> list[Listing]:
        """Сохраняет выдачу, убирает пропавшие объявления, возвращает новые."""
        now = time.time()
        known = self.known(item_key)
        first_run = self.conn.execute("SELECT 1 FROM checked WHERE item_key=?", (item_key,)).fetchone() is None
        new = []
        for l in listings:
            if l.id in known:
                self.conn.execute("UPDATE listings SET title=?, price=?, text=?, last_seen=? WHERE item_key=? AND id=?",
                                  (l.title, l.price, l.text, now, item_key, l.id))
            else:
                self.conn.execute(
                    "INSERT INTO listings (item_key, id, title, price, url, place, date, first_seen, last_seen, text) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (item_key, l.id, l.title, l.price, l.url, l.place, l.date, now, now, l.text))
                new.append(l)
        ids = [l.id for l in listings]
        self.conn.execute(f"DELETE FROM listings WHERE item_key=? AND id NOT IN ({','.join('?' * len(ids))})",
                          (item_key, *ids))
        self.conn.execute("INSERT OR REPLACE INTO checked VALUES (?,?)", (item_key, now))
        self.conn.commit()
        # При первой проверке модели всё «новое», уведомлять не о чем
        return [] if first_run else new

    def known(self, item_key: str) -> set[str]:
        return {r[0] for r in self.conn.execute("SELECT id FROM listings WHERE item_key=?", (item_key,))}

    def detail(self, listing_id: str) -> int | None:
        r = self.conn.execute("SELECT ok FROM details WHERE id=?", (listing_id,)).fetchone()
        return r[0] if r else None

    def set_detail(self, listing_id: str, ok: bool, reason: str = ""):
        self.conn.execute("INSERT OR REPLACE INTO details VALUES (?,?,?,?)", (listing_id, int(ok), reason, time.time()))
        self.conn.commit()

    def rows(self, item_key: str) -> list[Listing]:
        """Все сохранённые объявления модели, кроме отбракованных по странице объявления."""
        out = []
        for id_, title, price, url, place, text in self.conn.execute(
                "SELECT l.id, l.title, l.price, l.url, l.place, l.text FROM listings l "
                "LEFT JOIN details d ON d.id = l.id WHERE l.item_key=? AND (d.ok IS NULL OR d.ok=1)", (item_key,)):
            out.append(Listing(id=id_, title=title, price=price, url=url, text=text or title, place=place or ""))
        return out

    def checked_at(self, item_key: str) -> float | None:
        r = self.conn.execute("SELECT at FROM checked WHERE item_key=?", (item_key,)).fetchone()
        return r[0] if r else None
