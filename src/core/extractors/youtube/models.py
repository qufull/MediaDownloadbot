from dataclasses import dataclass, field

from .enums import YoutubeErrorCode
from ..abstractions import (
    AbstractDataModel,
    AbstractAudioModel,
    AbstractImageModel,
    AbstractVideoModel,
    AbstractResultModel,
)


@dataclass
class YoutubeData(AbstractDataModel):
    """Контейнер для данных медиа с YouTube."""
    pass


@dataclass
class YoutubeImage(AbstractImageModel):
    """Представляет миниатюру YouTube."""
    pass


@dataclass
class YoutubeVideo(AbstractVideoModel):
    """Представляет видео формат YouTube."""
    pass


@dataclass
class YoutubeAudio(AbstractAudioModel):
    """Представляет аудио формат YouTube."""
    pass


@dataclass
class YoutubeResult(AbstractResultModel):
    """Результат операций с YouTube."""
    code: YoutubeErrorCode = field(default=YoutubeErrorCode.SUCCESS)
