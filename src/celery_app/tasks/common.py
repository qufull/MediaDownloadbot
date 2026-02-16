# src/celery_app/tasks/common.py
import os

from collections import Counter
from dataclasses import dataclass
from urllib.parse import unquote, urlparse
from typing import Dict, List, Optional, Set, Tuple

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from src.core import (
    AbstractExtractor,
    VideoDictAnnotation,
    AudioDictAnnotation,
    ImageDictAnnotation,
)
from src.config import (
    reddit_extractor,
    rutube_extractor,
    tiktok_extractor,
    youtube_extractor,
    instagram_extractor,
    twitter_extractor,
    vk_extractor,
)


@dataclass
class MediaButton:
    """Универсальный класс для кнопок медиа-контента"""
    row: int
    label: str
    callback_data: str
    url: Optional[str] = None


class MediaProcessor:
    """Класс для обработки и подготовки медиа-контента"""

    @staticmethod
    def _exetract_image_name(image_url: str) -> Optional[str]:
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

    @staticmethod
    def parse_videos(
            videos: List[VideoDictAnnotation],
            cached_heights: Optional[Set[int]] = None
    ) -> List[MediaButton]:
        """
        Парсит видео данные и создает кнопки для разных качеств.

        Args:
            videos: Список видео форматов
            cached_heights: Set качеств (heights) которые уже закэшированы.
                           Для них добавляется 🚀

        Returns:
            Список кнопок MediaButton
        """
        import logging
        logger = logging.getLogger(__name__)

        if not videos:
            logger.warning("[parse_videos] Список видео пустой")
            return []

        if cached_heights is None:
            cached_heights = set()

        logger.info(f"[parse_videos] Получено видео форматов: {len(videos)}, cached: {cached_heights}")

        # нормализуем данные
        for v in videos:
            if v.get("language"):
                v["language"] = v["language"].lower().strip()
            if "language_preference" not in v or v["language_preference"] is None:
                v["language_preference"] = 0
            if "total_bitrate" not in v or v["total_bitrate"] is None:
                v["total_bitrate"] = 0

        # сортируем по правилу:
        # 1. has_audio (True > False)
        # 2. language_preference (больше = лучше)
        # 3. total_bitrate (больше = лучше)
        videos_sorted = sorted(
            videos,
            key=lambda v: (v.get("has_audio", False), v["language_preference"], v["total_bitrate"]),
            reverse=True
        )

        # группируем по высоте (оставляем лучший вариант для каждой высоты)
        video_by_quality: Dict[int, VideoDictAnnotation] = {}
        for video in videos_sorted:
            height = video.get("height")
            if height:
                # если на эту высоту еще нет видео → добавляем лучший
                if height not in video_by_quality:
                    video_by_quality[height] = video
                    logger.debug(
                        f"[parse_videos] Добавлено: {height}p, has_audio={video.get('has_audio')}, name={video.get('name')}")

        logger.info(
            f"[parse_videos] Уникальных качеств: {len(video_by_quality)}, heights: {list(video_by_quality.keys())}")

        # создаем кнопки - всегда показываем качество
        buttons = []
        qualities = sorted(video_by_quality.keys(), reverse=True)

        for quality in qualities:
            video = video_by_quality[quality]
            height = video.get("height")

            # 🚀 добавляем ракету если качество закэшировано
            if quality in cached_heights:
                label = f"🚀 {height}p (мгновенно)"
            else:
                label = f"🎬 {height}p"

            buttons.append(MediaButton(
                row=1,
                label=label,
                callback_data=f"video:{video['id']}"
            ))

        logger.info(f"[parse_videos] Создано кнопок: {len(buttons)}, с 🚀: {len(cached_heights & set(qualities))}")
        return buttons

    @staticmethod
    def parse_audios(audios: List[AudioDictAnnotation]) -> Optional["MediaButton"]:
        """Парсит аудио данные и создает кнопку"""
        if not audios:
            return None

        # нормализуем язык
        for audio in audios:
            lang = (audio.get("language") or "").lower().strip()
            audio["language"] = lang
            # если нет приоритета, ставим 0
            if "language_preference" not in audio or audio["language_preference"] is None:
                audio["language_preference"] = 0
            # если нет битрейта, ставим 0
            if "total_bitrate" not in audio or audio["total_bitrate"] is None:
                audio["total_bitrate"] = 0

        # выбираем лучший трек: сначала по language_preference, потом по bitrate
        best_audio = max(
            audios,
            key=lambda a: (a["language_preference"], a["total_bitrate"])
        )

        return MediaButton(
            row=2,
            label="🎵 Audio",
            callback_data=f"audio:{best_audio['id']}",
            url=best_audio.get("url")
        )

    @staticmethod
    def parse_thumbnails(thumbnails: List[ImageDictAnnotation]) -> Optional[MediaButton]:
        """Парсит превью и создает кнопку"""
        if not thumbnails:
            return None

        # Берем лучшее превью (последний элемент обычно лучший)
        best_thumbnail = thumbnails[-1]
        return MediaButton(
            row=3,
            label="🖼️ Preview",
            callback_data=f"thumbnail:{best_thumbnail['id']}",
            url=best_thumbnail.get("url")
        )

    @staticmethod
    def parse_images(images: List[ImageDictAnnotation]) -> Optional[MediaButton]:
        """Парсит изображения и создает кнопку"""
        if not images:
            return None

        # Группируем изображения по высоте и берем лучшее качество
        images_by_name: Dict[int, List[ImageDictAnnotation]] = {}
        for image in images:
            filename = MediaProcessor._exetract_image_name(image_url=image["url"])
            if filename not in images_by_name:
                images_by_name[filename] = []
            images_by_name[filename].append(image)

        # Находим максимальное качество
        best_images = []
        for image_name in images_by_name.keys():
            best_images.append(
                images_by_name[image_name][-1]
            )

        if not best_images:
            return None

        # Берем первое изображение лучшего качества для превью
        best_image = best_images[0]
        label = "🖼️ Image" if len(best_images) == 1 else "🖼️ Images"

        return MediaButton(
            row=1,
            label=label,
            callback_data="image",
            url=best_image.get("url")
        )


def create_keyboard_layout(buttons: List[Optional[MediaButton]]) -> List[Tuple[int, str, str]]:
    """Создает раскладку клавиатуры из кнопок"""
    keyboard_data = []
    for button in buttons:
        if button:
            keyboard_data.append((button.row, button.label, button.callback_data))
    return keyboard_data


def _get_adjust_list(data: List[Tuple[int, str, str]]) -> List[int]:
    counter = Counter(item[0] for item in data)
    adjust_list = []
    for item in counter.values():
        quotient = item // 3
        remainder = item % 3
        if quotient != 0:
            adjust_list.extend([3] * quotient)
        if remainder != 0:
            adjust_list.append(remainder)
    return adjust_list


def get_inline_keyboard(data: List[Tuple[int, str, str]]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for _, text, callback_data in data:
        builder.button(text=text, callback_data=callback_data)
    builder.adjust(*_get_adjust_list(data=data))
    return builder.as_markup()


def get_extractor(service: str) -> AbstractExtractor:
    extractors = {
        "rutube": rutube_extractor,
        "reddit": reddit_extractor,
        "tiktok": tiktok_extractor,
        "youtube": youtube_extractor,
        "instagram": instagram_extractor,
        "twitter": twitter_extractor,
        "vk": vk_extractor,
    }

    if service not in extractors:
        raise ValueError(f"Unsupported service: {service}")

    return extractors[service]