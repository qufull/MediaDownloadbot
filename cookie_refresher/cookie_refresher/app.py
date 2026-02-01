import json
import logging
import time
from pathlib import Path
from typing import Any, List, Sequence

from undetected_chromedriver import Chrome

from .normalize import normalize_cookie_dict, normalize_cookie_domain, CookieNormalizeConfig

from .annotation import CookieDict
from .driver import DriverConfig
from .models import CookieConfig

logger = logging.getLogger("refresher")


class CookieRefresher:
    """
    Класс для автоматического обновления cookie через браузер undetected_chromedriver.

    Позволяет:
      - загружать cookie из JSON,
      - обновлять cookie на целевых сайтах,
      - сохранять обновлённые cookie в JSON и Netscape формат.

    Атрибуты:
        configs (List[CookieConfig]): список конфигураций для обновления cookie.
        cookies_dir (Path): директория для сохранения cookies.
    """

    def __init__(self, cookies_dir: Path, configs: List[CookieConfig]) -> None:
        """
        Инициализация CookieRefresher.

        Args:
            cookies_dir: Директория для сохранения обновлённых cookies.
            configs: Список конфигураций для обновления cookie.
        """
        self.cookies_dir: Path = cookies_dir
        self.configs: List[CookieConfig] = configs

        logger.info("CookieRefresher инициализирован (cookies_dir=%s)", cookies_dir)

    # ----------------------------- STATIC HELPERS -----------------------------

    @staticmethod
    def read_json_cookies(cookie_path: Path) -> List[CookieDict]:
        """
        Загружает cookie из JSON-файла.

        Args:
            cookie_path: Путь к JSON-файлу.

        Returns:
            Список cookie в формате Selenium.
        """
        with cookie_path.open("r", encoding="utf-8") as file:
            cookies: List[CookieDict] = json.load(file)
            logger.debug("Загружено %d cookie из %s", len(cookies), cookie_path)
        return cookies

    @staticmethod
    def write_json_cookies(
        cookie_path: Path, cookie_data: Sequence[dict[str, Any]]
    ) -> None:
        """
        Сохраняет cookie в JSON-файл.

        Args:
            cookie_path: Путь к файлу для записи.
            cookie_data: Список cookie.
        """
        with cookie_path.open("w", encoding="utf-8") as file:
            json.dump(cookie_data, file, indent=2)
        logger.debug(
            "Сохранено %d cookie в JSON файл %s", len(cookie_data), cookie_path
        )

    @staticmethod
    def write_netscape_cookies(cookie_path: Path, cookie_data: str) -> None:
        """
        Сохраняет cookie в Netscape формате в текстовый файл.

        Args:
            cookie_path: Путь к файлу для записи.
            cookie_data: Cookie в формате Netscape.
        """
        with cookie_path.open("w", encoding="utf-8") as file:
            file.write(cookie_data)
        logger.debug(
            "Сохранено %d символов cookie в Netscape файл %s",
            len(cookie_data),
            cookie_path,
        )

    @staticmethod
    def json_to_netscape(cookies: Sequence[dict[str, Any]]) -> str:
        """
        Конвертирует cookie из JSON (Selenium) в Netscape-формат для curl, wget и др.

        Args:
            cookies: Список cookie.

        Returns:
            Строка в Netscape-формате.
        """
        netscape_lines = [
            "# Netscape HTTP Cookie File",
            "# https://curl.haxx.se/rfc/cookie_spec.html",
            "# Это сгенерированный файл! Редактировать нельзя.",
            "",
        ]

        cfg = CookieNormalizeConfig(
            strip_common_prefixes=True,
            keep_leading_dot=True,  # для Netscape удобно с точкой
            aggressive=False,
        )

        for cookie in cookies:
            name = cookie.get("name", "")
            path = cookie.get("path", "/")
            value = cookie.get("value", "")
            domain = normalize_cookie_domain(cookie.get("domain", ""), url_hint=None, cfg=cfg)
            secure = cookie.get("secure", False)
            session = cookie.get("session", False)
            host_only = cookie.get("hostOnly", False)
            expiration_date = cookie.get("expirationDate")

            include_subdomains_flag = "FALSE" if host_only else "TRUE"
            secure_flag = "TRUE" if secure else "FALSE"

            expiry = (
                "0"
                if (session or expiration_date is None)
                else str(int(float(expiration_date)))
            )

            netscape_line = "\t".join(
                [
                    domain,
                    include_subdomains_flag,
                    path,
                    secure_flag,
                    expiry,
                    name,
                    value,
                ]
            )
            netscape_lines.append(netscape_line)

        netscape_lines.append("")  # Пустая строка в конце
        return "\n".join(netscape_lines)

    # ----------------------------- INTERNAL METHODS -----------------------------

    def _get_cookies(self, cookie_path: Path) -> List[CookieDict]:
        """
        Загружает и фильтрует cookie для Selenium, убирая лишние ключи и исправляя sameSite.

        Args:
            cookie_path: Путь к JSON-файлу cookie.

        Returns:
            Очищенный список cookie.
        """
        cookies = self.read_json_cookies(cookie_path)
        filtered_cookies: List[CookieDict] = []

        for cookie in cookies:
            for key in ["storeId", "hostOnly", "session", "partitionKey"]:
                cookie.pop(key, None)

            same_site = cookie.get("sameSite")
            if same_site not in ["Lax", "Strict", "None"]:
                cookie["sameSite"] = "None"

            if not cookie.get("name") or not cookie.get("value"):
                logger.debug(
                    "Пропущена некорректная cookie без имени или значения: %s", cookie
                )
                continue

            filtered_cookies.append(cookie)

        logger.debug(
            "Отфильтровано %d корректных cookie из %d",
            len(filtered_cookies),
            len(cookies),
        )
        return filtered_cookies

    def _get_driver(self) -> Chrome:
        """
        Создаёт и настраивает undetected_chromedriver.

        Returns:
            Объект Chrome драйвера.
        """
        driver_manager = DriverConfig()
        driver = driver_manager.get_undetected_driver(
            headless=True, use_fake_useragent=True
        )
        logger.debug("Создан экземпляр Chrome драйвера")
        return driver

    # ----------------------------- MAIN LOGIC -----------------------------

    def refresh_cookies(self) -> None:
        """
        Обновляет cookie для всех конфигураций.

        Последовательность:
          - Открыть браузер
          - Загрузить старые cookie из файлов
          - Добавить cookie в браузер
          - Обновить страницу
          - Извлечь обновлённые cookie
          - Сохранить в JSON и Netscape файл
        """
        all_cookies: List[dict[str, Any]] = []

        for config in self.configs:
            logger.info("Обновление cookie для %s (%s)", config.name, config.url)
            with self._get_driver() as driver:
                for path in config.paths:
                    cookies = self._get_cookies(path)
                    logger.info("Добавляется %d cookie в браузер", len(cookies))

                    driver.get(str(config.url))

                    for cookie in cookies:
                        try:
                            driver.add_cookie(cookie)
                        except Exception as e:
                            logger.warning(
                                "Не удалось добавить cookie %s: %s",
                                cookie.get("name"),
                                e,
                            )

                    driver.refresh()
                    time.sleep(2.5)

                    new_cookies = driver.get_cookies()

                    cfg = CookieNormalizeConfig(
                        strip_common_prefixes=True,
                        keep_leading_dot=False,  # для Selenium add_cookie обычно лучше без точки
                        aggressive=False,
                    )

                    new_cookies = [normalize_cookie_dict(c, url_hint=str(config.url), cfg=cfg) for c in new_cookies]

                    all_cookies.extend(new_cookies)

                    self.write_json_cookies(cookie_path=path, cookie_data=new_cookies)
                    logger.info(
                        "Обновлено %d cookie в JSON файл %s", len(new_cookies), path
                    )

        netscape_cookies = self.json_to_netscape(cookies=all_cookies)
        netscape_path = self.cookies_dir / "cookies.txt"

        self.write_netscape_cookies(
            cookie_path=netscape_path, cookie_data=netscape_cookies
        )
        logger.info(
            "Сохранено %d cookie в Netscape файл %s", len(all_cookies), netscape_path
        )
