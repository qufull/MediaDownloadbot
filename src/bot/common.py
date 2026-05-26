import os
from enum import Enum
from typing import Optional
from urllib.parse import unquote, urlparse


class ServiceType(Enum):
    """
    Перечисление типов поддерживаемых сервисов.

    Используется для идентификации типа сервиса по домену URL
    и соответствующей обработки контента.

    Examples:
        >>> ServiceType.YOUTUBE
        <ServiceType.YOUTUBE: 'youtube'>

        >>> ServiceType.YOUTUBE.value
        'youtube'

        >>> ServiceType('youtube')
        <ServiceType.YOUTUBE: 'youtube'>
    """

    YOUTUBE = "youtube"
    """YouTube - видеохостинг."""

    INSTAGRAM = "instagram"
    """Instagram - социальная сеть для фото и видео."""

    REDDIT = "reddit"
    """Reddit - социальный новостной сайт."""

    RUTUBE = "rutube"
    """Rutube - российский видеохостинг."""

    TIKTOK = "tiktok"
    """TikTok - платформа для коротких видео."""

    TWITTER = "twitter"

    VK = "vk"
    """ВКонтакте - социальная сеть."""
    PINTEREST = "pinterest"
    """Pinterest - визуальная социальная сеть."""

    FACEBOOK = "facebook"
    """Facebook - социальная сеть."""

    LIKEE = "likee"
    """Likee - платформа для коротких видео."""

    UNSUPPORTED = "unsupported"
    """Неподдерживаемый сервис."""


def exetract_image_name(image_url: str) -> Optional[str]:
    """Извлекает наименование изображения из URL."""
    parsed_url = urlparse(image_url)
    path = parsed_url.path

    if not path:
        return None

    decoded_path = unquote(path)
    filename = os.path.basename(decoded_path)

    if filename:
        return filename

    return path