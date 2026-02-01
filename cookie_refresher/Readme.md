# Cookie Refresher - Docker версия

## Быстрый старт

### 1. Структура папок

```
media-bot/
├── docker-compose.yaml      # Твой media-bot + cookie-refresher
├── .env
├── Dockerfile               # Основной бот
├── cookie_refresher/        # <- Положи эту папку сюда
│   ├── Dockerfile
│   ├── entrypoint.sh
│   ├── requirements.txt
│   ├── run_refresh_docker.py
│   └── cookie_refresher/
│       ├── __init__.py
│       ├── app.py
│       ├── driver.py
│       └── ...
```

Volume `user_data` содержит:
```
/var/lib/docker/volumes/media-bot_user_data/_data/
├── cookies.txt              # Результат (Netscape формат, для бота)
├── tiktok_cookies.json      # JSON куки TikTok (из браузера)
└── instagram_cookies.json   # JSON куки Instagram (из браузера)
```

### 2. Добавь в docker-compose.yaml media-bot

```yaml
services:
  # redis, telegram-bot-api, bot - как у тебя

  cookie-refresher:
    build:
      context: ./cookie_refresher
      dockerfile: Dockerfile
    container_name: cookie-refresher
    volumes:
      - user_data:/app/data  # Общий volume с ботом
    environment:
      - REFRESH_SITES=tiktok,instagram
    profiles:
      - refresh  # Не запускается автоматически
    restart: "no"

volumes:
  redis_data:
  media_storage:
  user_data:
```

### 3. Экспортируй куки

На локальном компьютере:
1. Установи Cookie-Editor в браузере
2. Зайди на tiktok.com → Export → JSON
3. Сохрани как `tiktok_cookies.json`
4. То же для instagram.com

Загрузи на сервер в volume `user_data`:
```bash
# Найди путь к volume
docker volume inspect media-bot_user_data

# Скопируй файлы (путь из _data выше)
sudo cp tiktok_cookies.json /var/lib/docker/volumes/media-bot_user_data/_data/
sudo cp instagram_cookies.json /var/lib/docker/volumes/media-bot_user_data/_data/
```

### 4. Запуск

```bash
# Собрать образ
docker-compose build cookie-refresher

# Запустить обновление куки
docker-compose run --rm cookie-refresher

# Или с profile
docker-compose --profile refresh up cookie-refresher
```

### 5. Автозапуск через cron

```bash
crontab -e
```

Добавь:
```bash
# Каждое воскресенье в 3:00
0 3 * * 0 cd /path/to/media-bot && docker-compose run --rm cookie-refresher >> /var/log/cookie-refresher.log 2>&1
```

### 6. Автозапуск через systemd

```bash
# /etc/systemd/system/cookie-refresher.service
[Unit]
Description=Cookie Refresher
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
WorkingDirectory=/path/to/media-bot
ExecStart=/usr/bin/docker-compose run --rm cookie-refresher

[Install]
WantedBy=multi-user.target
```

```bash
# /etc/systemd/system/cookie-refresher.timer
[Unit]
Description=Run Cookie Refresher weekly

[Timer]
OnCalendar=Sun *-*-* 03:00:00
Persistent=true

[Install]
WantedBy=timers.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable cookie-refresher.timer
sudo systemctl start cookie-refresher.timer
```

## Проверка

```bash
# Логи
docker-compose logs cookie-refresher

# Проверить куки в media-bot
docker exec media-bot cat /app/data/cookies.txt | head

# Запустить вручную
docker-compose run --rm cookie-refresher
```

## Переменные окружения

| Переменная | Описание | По умолчанию |
|------------|----------|--------------|
| REFRESH_SITES | Сайты через запятую | tiktok,instagram |

## Troubleshooting

### Chrome не запускается
```
Error: Chrome failed to start
```
Убедись что в Dockerfile есть все зависимости Chrome.

### Куки не найдены
```
✗ tiktok: файл не найден
```
Проверь что JSON файлы лежат в правильном месте и volume смонтирован.

### Куки не обновляются
Проверь что ты залогинен на сайте когда экспортируешь куки.
Куки должны содержать session cookies.
