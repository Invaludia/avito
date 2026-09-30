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
        self.conn.execute("CREATE TABLE IF NOT EXISTS checked (item_key TEXT PRIMARY KEY, at REAL)")
        self.conn.commit()

    def save(self, item_key: str, listings: list[Listing]) -> list[Listing]:
        """Сохраняет выдачу, убирает пропавшие объявления, возвращает новые."""
        now = time.time()
        known = {r[0] for r in self.conn.execute("SELECT id FROM listings WHERE item_key=?", (item_key,))}
        first_run = self.conn.execute("SELECT 1 FROM checked WHERE item_key=?", (item_key,)).fetchone() is None
        new = []
        for l in listings:
            if l.id in known:
                self.conn.execute("UPDATE listings SET title=?, price=?, last_seen=? WHERE item_key=? AND id=?",
                                  (l.title, l.price, now, item_key, l.id))
            else:
                self.conn.execute("INSERT INTO listings VALUES (?,?,?,?,?,?,?,?,?)",
                                  (item_key, l.id, l.title, l.price, l.url, l.place, l.date, now, now))
                new.append(l)
        ids = [l.id for l in listings]
        self.conn.execute(f"DELETE FROM listings WHERE item_key=? AND id NOT IN ({','.join('?' * len(ids))})",
                          (item_key, *ids))
        self.conn.execute("INSERT OR REPLACE INTO checked VALUES (?,?)", (item_key, now))
        self.conn.commit()
        # При первой проверке модели всё «новое», уведомлять не о чем
        return [] if first_run else new

    def top(self, item_key: str, limit: int, by_price: bool) -> list[tuple]:
        order = "price IS NULL, price" if by_price else "first_seen DESC"
        return self.conn.execute(
            f"SELECT title, price, url, place FROM listings WHERE item_key=? ORDER BY {order} LIMIT ?",
            (item_key, limit)).fetchall()

    def checked_at(self, item_key: str) -> float | None:
        r = self.conn.execute("SELECT at FROM checked WHERE item_key=?", (item_key,)).fetchone()
        return r[0] if r else None
