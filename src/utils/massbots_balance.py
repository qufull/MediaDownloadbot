import massbots
from src.settings import AppSettings

settings = AppSettings()

def get_massbots_balance():
    """
    Возвращает баланс и валюту из MassBots API.
    Бросает исключение, если токен не задан или API ответил ошибкой.
    """
    token = settings.massbots.token
    if not token:
        raise RuntimeError("massbots_token не задан в настройках")

    api = massbots.Api(token)
    bal = api.balance()   # по SDK из документации

    return bal