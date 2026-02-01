from dataclasses import dataclass, field

from .enums import RutubeErrorCode
from ..abstractions import (
    AbstractDataModel,
    AbstractAudioModel,
    AbstractImageModel,
    AbstractVideoModel,
    AbstractResultModel,
)


@dataclass
class RutubeData(AbstractDataModel):
    """Контейнер для данных медиа с Rutube."""
    pass


@dataclass
class RutubeImage(AbstractImageModel):
    """Представляет миниатюру изображения Rutube."""
    pass


@dataclass
class RutubeVideo(AbstractVideoModel):
    """Представляет видео формат Rutube."""
    pass


@dataclass
class RutubeAudio(AbstractAudioModel):
    """Представляет аудио формат Rutube."""
    pass


@dataclass
class RutubeResult(AbstractResultModel):
    """Результат операций с Rutube."""
    code: RutubeErrorCode = field(default=RutubeErrorCode.SUCCESS)
