from dataclasses import dataclass, field

from .enums import TikTokErrorCode
from ..abstractions import (
    AbstractDataModel,
    AbstractAudioModel,
    AbstractImageModel,
    AbstractVideoModel,
    AbstractResultModel,
)


@dataclass
class TikTokData(AbstractDataModel):
    """Контейнер для данных медиа с TikTok."""
    pass


@dataclass
class TikTokImage(AbstractImageModel):
    """Представляет изображение TikTok."""
    pass


@dataclass
class TikTokVideo(AbstractVideoModel):
    """Представляет видео формат TikTok."""
    pass


@dataclass
class TikTokAudio(AbstractAudioModel):
    """Представляет аудио формат TikTok."""
    pass


@dataclass
class TikTokResult(AbstractResultModel):
    """Результат операций с TikTok."""
    code: TikTokErrorCode = field(default=TikTokErrorCode.SUCCESS)
