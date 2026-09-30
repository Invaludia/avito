@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist .env (
  set /p TOKEN=Вставь токен бота от BotFather и нажми Enter: 
  call :mkenv
)
echo Устанавливаю библиотеки...
py -m pip install -q -r requirements.txt
echo Бот запущен. Не закрывай это окно, пока бот нужен.
py -m avito_bot
pause
exit /b

:mkenv
> .env echo BOT_TOKEN=%TOKEN%
>> .env echo CATALOG=https://docs.google.com/spreadsheets/d/1jW3sHsxgwMMbmZcgJCpjYZiYiqKJciXgYDNbiYMf79k/edit
>> .env echo ALLOWED_USERS=
>> .env echo DB_PATH=avito.sqlite3
exit /b
