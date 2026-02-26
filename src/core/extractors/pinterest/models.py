from dataclasses import dataclass, field

from .enums import PinterestErrorCode
from ..abstractions import (
    AbstractDataModel,
    AbstractAudioModel,
    AbstractImageModel,
    AbstractVideoModel,
    AbstractResultModel,
)


@dataclass
class PinterestData(AbstractDataModel):
    """Контейнер с медиа-данными Pinterest."""
    pass


@dataclass
class PinterestImage(AbstractImageModel):
    """Объект изображения Pinterest."""
    pass


@dataclass
class PinterestVideo(AbstractVideoModel):
    """Объект видео Pinterest."""
    pass


@dataclass
class PinterestAudio(AbstractAudioModel):
    """Объект аудио Pinterest (если потребуется)."""
    pass


@dataclass
class PinterestResult(AbstractResultModel):
    """Результат выполнения операций Pinterest."""
    code: PinterestErrorCode = field(default=PinterestErrorCode.SUCCESS)