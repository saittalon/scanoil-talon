
## Telegram-бот
1) Создай бота у @BotFather и получи токен.
2) В PowerShell:
   $env:BOT_TOKEN="123:ABC" 
   python bot.py

QR код на талоне содержит код талона. Отправь код в чат — бот погасит талон.

## Печать талонов
Вкладка клиента → Талоны → "Печать талонов (A4)". Откроется страница для печати (A4, 20 талонов/лист).
.


## Логи и backup

- В панели директора появился раздел **Логи и backup**.
- Там можно смотреть журнал действий пользователей, скачивать backup ZIP и вручную отправлять backup в Supabase Storage.
- Backup содержит JSON/CSV дампы таблиц базы и, если включён `BACKUP_INCLUDE_FILES=1`, PDF-файлы из Supabase Storage.
- Логи приложения пишутся в `logs/app.log`.

### Автозаливка backup в облако

Добавь в Railway Variables:

```
BACKUP_UPLOAD_TO_SUPABASE=1
BACKUP_SUPABASE_BUCKET=backups
BACKUP_SUPABASE_PATH=auto
BACKUP_KEEP_LAST=30
BACKUP_INCLUDE_FILES=1
```

Что это делает:
- каждый запуск `python scripts/run_backup.py` создаёт ZIP;
- ZIP сохраняется локально в `backups/`;
- ZIP автоматически загружается в Supabase Storage bucket `backups`;
- старые backup-файлы сверх лимита `BACKUP_KEEP_LAST` удаляются.

### Важно в Supabase

1. Создай Storage bucket с именем **backups**.
2. Держи bucket приватным.
3. Убедись, что в Railway заданы `SUPABASE_URL` и `SUPABASE_SERVICE_ROLE_KEY`.

### Ручной backup из консоли

```bash
python scripts/run_backup.py
```

Архив сохранится в папку `backups/` или в путь из `BACKUP_OUTPUT_DIR`, а при включённом `BACKUP_UPLOAD_TO_SUPABASE=1` ещё и загрузится в Supabase Storage.
