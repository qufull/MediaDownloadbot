from dataclasses import dataclass, field

from .enums import RedditErrorCode
from ..abstractions import (
    AbstractDataModel,
    AbstractAudioModel,
    AbstractImageModel,
    AbstractVideoModel,
    AbstractResultModel,
)


@dataclass
class RedditData(AbstractDataModel):
    """Контейнер данных о медиа с Reddit."""
    pass


@dataclass
class RedditImage(AbstractImageModel):
    """Представление изображения с Reddit."""
    pass


@dataclass
class RedditVideo(AbstractVideoModel):
    """Представление видео с Reddit."""
    pass


@dataclass
class RedditAudio(AbstractAudioModel):
    """Представление аудио с Reddit."""
    pass


@dataclass
class RedditResult(AbstractResultModel):
    """Результат операций с Reddit."""
    code: RedditErrorCode = field(default=RedditErrorCode.SUCCESS)
