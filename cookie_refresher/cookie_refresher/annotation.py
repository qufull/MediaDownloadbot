from typing import NotRequired, TypedDict


class CookieDict(TypedDict):
    """
    TypedDict, описывающий структуру cookie, возвращаемого Selenium / undetected_chromedriver.

    Поля соответствуют стандарту Chrome DevTools Protocol (CDP) и Selenium `driver.get_cookies()`.

    Обязательные поля:
        name (str): Имя cookie.
        value (str): Значение cookie.
        domain (str): Домен, для которого установлено cookie.
        path (str): Путь, на который распространяется cookie.
        secure (bool): True, если cookie доступно только по HTTPS.
        httpOnly (bool): True, если cookie недоступно через JavaScript (Document.cookie).
        sameSite (str): Политика SameSite — 'Lax', 'Strict' или 'None'.

    Необязательные поля (NotRequired):
        expiry (int): Время истечения cookie в формате Unix timestamp (секунды).
        expirationDate (float): Аналог expiry, может встречаться в CDP (секунды с плавающей точкой).
        session (bool): True, если cookie является сессионным (удаляется при закрытии браузера).
        hostOnly (bool): True, если cookie установлено только для точного домена, без поддоменов.
        storeId (str): Идентификатор "хранилища" cookie (например, профиля браузера).
    """

    path: str
    name: str
    value: str
    domain: str
    secure: bool
    sameSite: str
    httpOnly: bool

    expiry: NotRequired[int]
    storeId: NotRequired[str]
    session: NotRequired[bool]
    hostOnly: NotRequired[bool]
    expirationDate: NotRequired[float]
