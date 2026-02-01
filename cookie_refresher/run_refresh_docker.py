#!/usr/bin/env python3
"""
Cookie Refresher - Docker версия.

Куки хранятся в /app/data/ (volume user_data)
Результат сохраняется в /app/data/cookies.txt
Этот volume общий с media-bot контейнером.
"""

import logging
import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from cookie_refresher import CookieRefresher, CookieConfig

# === НАСТРОЙКИ ===

# Volume user_data монтируется в /app/data
COOKIES_DIR = Path("/app/cookies")

# Конфигурации (читаем из переменных окружения какие сайты включены)
SITES = os.environ.get("REFRESH_SITES", "tiktok,instagram").split(",")

# === ЛОГИ ===

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger("cookie_refresher")


def build_configs() -> list:
    """Создаёт конфиги для включённых сайтов."""
    site_configs = {
        "tiktok": CookieConfig(
            name="tiktok",
            url="https://www.tiktok.com",
            paths=[COOKIES_DIR / "tiktok_cookies.json"],
        ),
        "instagram": CookieConfig(
            name="instagram",
            url="https://www.instagram.com",
            paths=[COOKIES_DIR / "instagram_cookies.json"],
        ),
        "reddit": CookieConfig(
            name="reddit",
            url="https://www.reddit.com",
            paths=[COOKIES_DIR / "reddit_cookies.json"],
        ),
        "twitter": CookieConfig(
            name="twitter",
            url="https://x.com",  # можно и https://twitter.com, но x.com сейчас чаще актуален
            paths=[COOKIES_DIR / "twitter_cookies.json"],
        ),
        "x": CookieConfig(
            name="twitter",
            url="https://x.com",
            paths=[COOKIES_DIR / "twitter_cookies.json"],
        ),
        "youtube": CookieConfig(
            name="youtube",
            url="https://www.youtube.com",
            paths=[
                COOKIES_DIR / "youtube_cookies.json",  # источник
            ],
        ),
    }
    
    configs = []
    for site in SITES:
        site = site.strip().lower()
        if site in site_configs:
            config = site_configs[site]
            # Проверяем существование файла
            if all(p.exists() for p in config.paths):
                configs.append(config)
                logger.info(f"✓ {site}: включён")
            else:
                logger.warning(f"✗ {site}: файл не найден ({config.paths[0]})")
        else:
            logger.warning(f"✗ {site}: неизвестный сайт")
    
    return configs


def main():
    logger.info("=" * 50)
    logger.info("Cookie Refresher - Docker")
    logger.info("=" * 50)
    
    # Проверяем директорию
    COOKIES_DIR.mkdir(parents=True, exist_ok=True)
    
    # Строим конфиги
    configs = build_configs()
    
    if not configs:
        logger.error("Нет валидных конфигураций!")
        logger.error("")
        logger.error("Создай JSON файлы с куками:")
        logger.error("1. Экспортируй куки из браузера (Cookie-Editor)")
        logger.error(f"2. Положи в volume: {COOKIES_DIR}/")
        logger.error("   - tiktok_cookies.json")
        logger.error("   - instagram_cookies.json")
        sys.exit(1)
    
    try:
        refresher = CookieRefresher(
            cookies_dir=COOKIES_DIR,
            configs=configs,
        )
        refresher.refresh_cookies()
        
        logger.info("=" * 50)
        logger.info("✅ Куки обновлены!")
        logger.info(f"📁 Файл: {COOKIES_DIR / 'cookies.txt'}")
        logger.info("=" * 50)
        
    except Exception as e:
        logger.exception(f"❌ Ошибка: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
