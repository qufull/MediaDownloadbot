import logging
import random

from undetected_chromedriver import Chrome, ChromeOptions

from .enums import FALLBACK_USER_AGENTS

logger = logging.getLogger("chrome")


class DriverConfig:
    """
    Класс для конфигурации и создания экземпляра undetected_chromedriver.Chrome.

    Позволяет:
      - выбирать случайный User-Agent (с поддержкой fake-useragent и fallback);
      - настраивать браузер (headless, прокси, отключение изображений, размеры окна и др.).
    """

    @staticmethod
    def safe_get_random_ua(
        use_fake: bool = True,
        explicit_ua: str | None = None,
    ) -> str:
        """
        Возвращает User-Agent для браузера.

        Args:
            use_fake: Использовать пакет fake-useragent, если True.
            explicit_ua: Если задан, используется этот User-Agent с приоритетом.

        Returns:
            Строка User-Agent.
        """
        if explicit_ua:
            logger.debug("Используется явный User-Agent.")
            return explicit_ua

        if use_fake:
            try:
                from fake_useragent import UserAgent

                ua = UserAgent()
                random_ua = ua.random
                if isinstance(random_ua, str) and random_ua.strip():
                    logger.debug("User-Agent получен из fake_useragent.")
                    return random_ua
            except Exception as e:
                logger.warning(
                    "Не удалось получить User-Agent через fake_useragent, используется fallback. Ошибка: %s",
                    e,
                )

        fallback_ua = random.choice(FALLBACK_USER_AGENTS)
        logger.info("Используется fallback User-Agent: %s", fallback_ua)
        return fallback_ua

    def get_undetected_driver(
        self,
        headless: bool = False,
        user_agent: str | None = None,
        use_fake_useragent: bool = True,
        proxy: str | None = None,
        disable_images: bool = True,
        window_size: str = "600,600",
        implicit_wait: int = 10,
        page_load_timeout: int = 60,
    ) -> Chrome:
        """
        Создаёт и настраивает экземпляр undetected_chromedriver.Chrome.

        Args:
            headless: Запуск в headless-режиме.
            user_agent: Явный User-Agent (приоритет над fake_useragent).
            use_fake_useragent: Использовать fake-useragent, если явный UA не задан.
            proxy: Прокси-сервер (например, "127.0.0.1:8080").
            disable_images: Отключить загрузку изображений для ускорения.
            window_size: Размер окна браузера "ширина,высота".
            implicit_wait: Время implicit wait в секундах.
            page_load_timeout: Таймаут загрузки страницы в секундах.

        Returns:
            Экземпляр Chrome драйвера.
        """
        final_ua = user_agent or self.safe_get_random_ua(use_fake=use_fake_useragent)
        logger.debug("Используемый User-Agent: %s", final_ua)

        options = ChromeOptions()

        if headless:
            options.add_argument("--headless=new")
            logger.debug("Включён headless режим.")

        options.add_argument(f"--window-size={window_size}")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--incognito")

        if final_ua:
            options.add_argument(f"--user-agent={final_ua}")

        if proxy:
            options.add_argument(f"--proxy-server=http://{proxy}")
            logger.debug("Включён прокси: %s", proxy)

        if disable_images:
            prefs = {"profile.managed_default_content_settings.images": 2}
            options.add_experimental_option("prefs", prefs)
            logger.debug("Отключена загрузка изображений.")

        logger.info("Запуск Chrome драйвера...")
        driver = Chrome(options=options)

        driver.implicitly_wait(implicit_wait)
        driver.set_page_load_timeout(page_load_timeout)
        logger.debug(
            "Настроены таймауты: implicit_wait=%d, page_load_timeout=%d",
            implicit_wait,
            page_load_timeout,
        )

        try:
            driver.delete_all_cookies()
            logger.debug("Удалены все cookies перед стартом.")
        except Exception as e:
            logger.warning("Не удалось удалить cookies перед стартом: %s", e)

        logger.info("Chrome драйвер успешно создан и готов к работе.")
        return driver
