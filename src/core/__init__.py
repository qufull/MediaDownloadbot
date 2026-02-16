"""
Модуль загрузчиков и экстракторов медиа-контента.

Предоставляет абстрактные классы и конкретные реализации для работы
с различными платформами (YouTube, Instagram, Reddit, Rutube, TikTok).
"""

from .downloader import Downloader
from .extractors.abstractions import (
    AbstractExtractor,
    AbstractDataModel,
    DataDictAnnotation,
    AbstractAudioModel,
    AbstractImageModel,
    AbstractVideoModel,
    AbstractResultModel,
    AudioDictAnnotation,
    ImageDictAnnotation,
    VideoDictAnnotation,
    ResultDictAnnotation,
    AbstractErrorCodeModel,
    CookieFileNotFoundError,
    ExtractInfoNotCalledError,
    UnsupportedContentTypeError,
)
from .extractors.instagram import (
    InstagramData,
    InstagramAudio,
    InstagramImage,
    InstagramVideo,
    InstagramResult,
    InstagramErrorCode,
    InstagramExtractor,
    InstagramSessionError,
    ContentType as InstagramContentType,
)
from .extractors.reddit import (
    RedditData,
    RedditAudio,
    RedditImage,
    RedditVideo,
    RedditResult,
    RedditErrorCode,
    RedditExtractor,
    InvalidRedditUrlError,
    ContentType as RedditContentType,
)
from .extractors.rutube import (
    RutubeData,
    RutubeAudio,
    RutubeImage,
    RutubeVideo,
    RutubeResult,
    RutubeErrorCode,
    RutubeExtractor,
    ContentType as RutubeContentType,
)
from .extractors.tiktok import (
    TikTokData,
    TikTokAudio,
    TikTokImage,
    TikTokVideo,
    TikTokResult,
    TikTokErrorCode,
    TikTokExtractor,
    ContentType as TikTokContentType,
)
from .extractors.youtube import (
    YoutubeData,
    YoutubeAudio,
    YoutubeImage,
    YoutubeVideo,
    YoutubeResult,
    YoutubeErrorCode,
    YoutubeExtractor,
    MassbotsApiError,
    ContentType as YoutubeContentType,
)

from .extractors.twitter import (
  TwitterExtractor
)

from .extractors.vk import (
  VKExtractor
)


__all__ = [
    # Базовый загрузчик
    "Downloader",

    # Абстрактные классы
    "AbstractExtractor",
    "AbstractDataModel",
    "AbstractAudioModel",
    "AbstractImageModel",
    "AbstractVideoModel",
    "AbstractResultModel",
    "AbstractErrorCodeModel",

    # Аннотации типов
    "DataDictAnnotation",
    "AudioDictAnnotation",
    "ImageDictAnnotation",
    "VideoDictAnnotation",
    "ResultDictAnnotation",

    # Общие исключения
    "CookieFileNotFoundError",
    "ExtractInfoNotCalledError",
    "UnsupportedContentTypeError",

    # Instagram компоненты
    "InstagramData",
    "InstagramAudio",
    "InstagramImage",
    "InstagramVideo",
    "InstagramResult",
    "InstagramErrorCode",
    "InstagramExtractor",
    "InstagramSessionError",
    "InstagramContentType",

    # Reddit компоненты
    "RedditData",
    "RedditAudio",
    "RedditImage",
    "RedditVideo",
    "RedditResult",
    "RedditErrorCode",
    "RedditExtractor",
    "InvalidRedditUrlError",
    "RedditContentType",

    # Rutube компоненты
    "RutubeData",
    "RutubeAudio",
    "RutubeImage",
    "RutubeVideo",
    "RutubeResult",
    "RutubeErrorCode",
    "RutubeExtractor",
    "RutubeContentType",

    # TikTok компоненты
    "TikTokData",
    "TikTokAudio",
    "TikTokImage",
    "TikTokVideo",
    "TikTokResult",
    "TikTokErrorCode",
    "TikTokExtractor",
    "TikTokContentType",

    # YouTube компоненты
    "YoutubeData",
    "YoutubeAudio",
    "YoutubeImage",
    "YoutubeVideo",
    "YoutubeResult",
    "YoutubeErrorCode",
    "YoutubeExtractor",
    "YoutubeContentType",
    "MassbotsApiError",

    "TwitterExtractor",

    "VKExtractor",
]


# Версия модуля
__version__ = "1.0.0"
__author__ = "AkhmedovH4mid"
__description__ = "Модуль для загрузки и извлечения медиа-контента с различных платформ"