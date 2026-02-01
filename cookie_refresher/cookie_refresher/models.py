from typing import List

from pydantic import BaseModel, FilePath, HttpUrl


class CookieConfig(BaseModel):
    """
    Конфигурация для хранения информации о cookie.

    Используется в CookieRefresher для:
      - указания имени конфигурации,
      - URL сайта, на котором обновляются cookie,
      - пути к JSON-файлу, где cookie хранятся.

    Атрибуты:
        name (str): уникальное имя конфигурации, используется для идентификации.
        url (HttpUrl): URL сайта, на котором необходимо обновить cookie.
        path (FilePath): путь к JSON-файлу с cookie.
    """

    name: str
    url: HttpUrl
    paths: List[FilePath]
