from dataclasses import dataclass, field

from .enums import InstagramErrorCode
from ..abstractions import (
    AbstractDataModel,
    AbstractAudioModel,
    AbstractImageModel,
    AbstractVideoModel,
    AbstractResultModel,
)


@dataclass
class InstagramData(AbstractDataModel):
    """Контейнер с медиа-данными Instagram."""
    pass


@dataclass
class InstagramImage(AbstractImageModel):
    """Объект изображения Instagram."""
    pass


@dataclass
class InstagramVideo(AbstractVideoModel):
    """Объект видео Instagram."""
    pass


@dataclass
class InstagramAudio(AbstractAudioModel):
    """Объект аудио Instagram (например, для сторис или рилсов)."""
    pass


@dataclass
class InstagramResult(AbstractResultModel):
    """Результат выполнения операций Instagram."""
    code: InstagramErrorCode = field(default=InstagramErrorCode.SUCCESS)
