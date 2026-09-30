@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist avito_bot\ (
  echo Не вижу файлов бота рядом с run.bat. Похоже, архив не распакован.
  echo Закрой окно, нажми на ZIP правой кнопкой - "Извлечь все", и запусти run.bat из распакованной папки.
  pause
  exit /b
)
if exist .git (
  echo Проверяю обновления...
  git pull --ff-only || echo Не удалось обновиться, запускаю текущую версию.
)
if not exist .env (
  set /p TOKEN=Вставь токен бота от BotFather и нажми Enter: 
  set /p SHEET=Вставь ссылку на Google Таблицу и нажми Enter: 
  call :mkenv
)
set PY=py
where py >nul 2>nul || set PY=python
%PY% --version || (echo Не нашёл Python. Установи его с python.org с галочкой Add to PATH. & pause & exit /b)
echo Проверяю обновления...
%PY% -m avito_bot.update
echo Устанавливаю библиотеки, это может занять пару минут...
%PY% -m pip install -q -r requirements.txt
echo Запускаю бота...
%PY% -m avito_bot
pause
exit /b

:mkenv
> .env echo BOT_TOKEN=%TOKEN%
>> .env echo CATALOG=%SHEET%
>> .env echo ALLOWED_USERS=
>> .env echo DB_PATH=avito.sqlite3
exit /b
