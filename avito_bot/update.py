"""Самообновление: скачивает свежую версию кода с GitHub и кладёт поверх папки.

Работает, только если репозиторий публичный. run.bat не трогает (он в этот момент запущен),
ничего личного тоже:
.env, база, профиль браузера и таблица в папке остаются как были.
"""
from __future__ import annotations

import io
import os
import shutil
import sys
import zipfile
from urllib.request import urlopen

URL = "https://codeload.github.com/Invaludia/avito/zip/refs/heads/main"
KEEP = {"run.bat", ".env", "avito.sqlite3", "browser_profile", "browser_profile_chrome", "browser_profile_msedge", "last_block.html", "bot.log", ".git"}


def main() -> int:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if os.path.isdir(os.path.join(root, ".git")):
        return 0  # папка из git: обновляет git pull в run.bat
    try:
        with urlopen(URL, timeout=30) as resp:
            data = resp.read()
    except Exception as e:
        print(f"Обновление пропущено ({e}). Запускаю текущую версию.")
        return 0
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        prefix = zf.namelist()[0].split("/")[0] + "/"
        changed = 0
        for info in zf.infolist():
            rel = info.filename[len(prefix):]
            if not rel or rel.split("/")[0] in KEEP:
                continue
            dest = os.path.join(root, *rel.split("/"))
            if info.is_dir():
                os.makedirs(dest, exist_ok=True)
                continue
            new = zf.read(info)
            try:
                with open(dest, "rb") as f:
                    if f.read() == new:
                        continue
            except FileNotFoundError:
                pass
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "wb") as f:
                f.write(new)
            changed += 1
    print(f"Обновлено файлов: {changed}" if changed else "Версия актуальная.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
