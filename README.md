

## Telegram-бот
1) Создай бота у @BotFather и получи токен.
2) В PowerShell:
   $env:BOT_TOKEN="123:ABC" 
   python bot.py

QR код на талоне содержит код талона. Отправь код в чат — бот погасит талон.

## Печать талонов
Вкладка клиента → Талоны → "Печать талонов (A4)". Откроется страница для печати (A4, 20 талонов/лист).
.


## Security settings

Set these variables in Railway / Render / server:

- `SECRET_KEY` = long random string
- `DIRECTOR_PASSWORD` = strong password for director
- `DEPUTY_PASSWORD` = strong password for deputy director
- `EXECUTOR_PASSWORD` = strong password for executor
- `SESSION_COOKIE_SECURE=1`
- `MAX_CONTENT_LENGTH=10485760`

Important: if users already exist in the database, passwords are not overwritten unless you explicitly set the environment variables above.
