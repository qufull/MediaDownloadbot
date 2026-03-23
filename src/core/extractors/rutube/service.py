import logging
from uuid import uuid4
from pathlib import Path
from urllib.parse import urlparse
from typing import Dict, List, Optional, Union

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError, ExtractorError

from ..abstractions import AbstractExtractor
from ..abstractions import CookieFileNotFoundError
from .enums import RutubeErrorCode, ContentType
from .models import (
    RutubeData,
    RutubeAudio,
    RutubeImage,
    RutubeVideo,
    RutubeResult,
)


logger = logging.getLogger("rutube")


class RutubeExtractor(AbstractExtractor):
    """
    Загрузчик медиа-контента с Rutube.
    
    Поддерживает загрузку видео и миниатюр с Rutube.
    Обрабатывает различные типы контента, включая shorts и обычные видео.
    """
    
    def __init__(
        self,
        proxy: Optional[str] = None,
        cookie_path: Optional[str] = None,
    ) -> None:
        """
        Инициализация загрузчика Rutube.
        
        Args:
            retries_count: Количество попыток повтора для загрузок
            proxy: URL прокси-сервера (опционально)
            cookie_path: Путь к файлу cookies (опционально)
            concurrent_download_count: Количество одновременных загрузок фрагментов
        """
        logger.info("Инициализация загрузчика Rutube")
        
        self.proxy = proxy
        self.cookies_path = Path(cookie_path) if cookie_path else None

        if self.cookies_path and not self.cookies_path.exists():
            error_msg = f"Файл cookie не найден: {self.cookies_path}"
            logger.error(error_msg)
            raise CookieFileNotFoundError(error_msg, RutubeErrorCode.COOKIE_FILE_NOT_FOUND)
        
        try:
            self.ydl_opts: Dict[str, Optional[Union[bool, str, Path]]] = {
                "quiet": True,
                "proxy": self.proxy,
                "no_warnings": False,
                "cookiefile": self.cookies_path,
                
                "playlistend": 1,
                "noplaylist": True,
            }
            
            self.unsupported_types: List[ContentType] = [
                ContentType.LIVE,
                ContentType.ACCOUNT,
                ContentType.PLAYLIST,
            ]
            
            self._data: Optional[RutubeData] = None
            self._last_result: Optional[RutubeResult] = None
            
            logger.debug("Загрузчик Rutube успешно инициализирован")
            
        except Exception as e:
            error_msg = f"Ошибка инициализации загрузчика Rutube: {e}"
            logger.error(error_msg)
            raise Exception(error_msg) from e
        
    def _classify_url(self, url: str) -> Optional[ContentType]:
        """
        Классификация типа контента URL Rutube.
        
        Args:
            url: URL Rutube для классификации
            
        Returns:
            ContentType или None если классификация не удалась
        """
        logger.debug(f"Классификация URL: {url}")
        
        try:
            parsed = urlparse(url=url)
            path = parsed.path.lower()
            path_parts = path.strip("/").split("/")
            
            if len(path_parts) < 2:
                logger.warning(f"Невалидный путь URL: {path}")
                return None
            
            if "/channel/" in path:
                result = ContentType.ACCOUNT
            elif "/shorts/" in path:
                result = ContentType.SHORTS
            elif "/live/" in path:
                result = ContentType.LIVE
            elif "/plst/" in path:
                result = ContentType.PLAYLIST
            else:
                result = ContentType.VIDEO
                
            logger.debug(f"URL классифицирован как: {result.value if result else 'unknown'}")
            return result
        
        except Exception as e:
            logger.error(f"Ошибка классификации URL: {e}")
            return None
        
    def _validate_rutube_url(self, url: str) -> bool:
        """
        Проверка валидности URL Rutube.
        
        Args:
            url: URL для проверки
            
        Returns:
            True если URL является валидным URL Rutube
        """
        try:
            parsed = urlparse(url)
            return parsed.netloc.endswith("rutube.ru")
        except Exception as e:
            logger.debug(f"Ошибка проверки URL: {e}")
            return False
        
    def extract_info(self, url: str) -> RutubeResult:
        """
        Извлечение информации о медиа из URL Rutube.
        
        Args:
            url: URL Rutube для извлечения информации
            
        Returns:
            RutubeResult: Результат, содержащий извлеченные данные медиа
        """
        logger.info(f"Извлечение информации из URL: {url}")
        
        self._data = RutubeData(url=url)
        
        # Проверка URL
        if not url or not isinstance(url, str):
            error_msg = "Предоставлен невалидный URL"
            logger.error(error_msg)
            self._last_result = RutubeResult(
                status="error",
                data=self._data,
                context=error_msg,
                code=RutubeErrorCode.EMPTY_URL,
            )
            return self._last_result
        
        if not self._validate_rutube_url(url):
            error_msg = "Невалидный или неподдерживаемый URL Rutube"
            logger.error(error_msg)
            self._last_result = RutubeResult(
                status="error",
                data=self._data,
                context=error_msg,
                code=RutubeErrorCode.INVALID_URL,
            )
            return self._last_result
        
        ydl_opts = self.ydl_opts.copy()
        ydl_opts['listformats'] = True
        
        # Классификация типа контента
        content_type = self._classify_url(url=url)
        if not content_type:
            error_msg = "Не удалось классифицировать тип контента URL"
            logger.error(error_msg)
            self._last_result = RutubeResult(
                status="error",
                data=self._data,
                context=error_msg,
                code=RutubeErrorCode.UNSUPPORTED_CONTENT_TYPE,
            )
            return self._last_result
        
        if content_type in self.unsupported_types:
            error_msg = f"Неподдерживаемый тип контента: {content_type.value}"
            logger.warning(error_msg)
            self._last_result = RutubeResult(
                status="error",
                data=self._data,
                context=error_msg,
                code=RutubeErrorCode.UNSUPPORTED_CONTENT_TYPE,
            )
            return self._last_result
           
        # Извлечение информации с использованием yt-dlp
        with YoutubeDL(params=ydl_opts) as ydl:
            try:
                logger.debug("Начало извлечения информации с yt-dlp")
                data = ydl.extract_info(url=url, download=False)
                logger.debug("Извлечение информации успешно завершено")

            except ExtractorError as e:
                err_str = str(e)
                if "404" in err_str or "not found" in err_str.lower():
                    error_msg = "Видео удалено или недоступно"
                else:
                    error_msg = f"Ошибка извлечения: {err_str}"
                logger.error(error_msg)
                self._last_result = RutubeResult(
                    status="error",
                    data=self._data,
                    context=error_msg,
                    code=RutubeErrorCode.EXTRACTOR_ERROR,
                )
                return self._last_result
            
            except DownloadError as e:
                err_str = str(e)
                if "404" in err_str or "not found" in err_str.lower():
                    error_msg = "Видео удалено или недоступно"
                else:
                    error_msg = f"Ошибка загрузки при извлечении: {err_str}"
                logger.error(error_msg)
                self._last_result = RutubeResult(
                    status="error",
                    data=self._data,
                    context=error_msg,
                    code=RutubeErrorCode.DOWNLOAD_ERROR,
                )
                return self._last_result

            except Exception as e:
                error_msg = f"Неожиданная ошибка при извлечении: {str(e)}"
                logger.error(error_msg)
                self._last_result = RutubeResult(
                    status="error",
                    data=self._data,
                    context=error_msg,
                    code=RutubeErrorCode.UNEXPECTED_ERROR,
                )
                return self._last_result
                
        # Проверка неподдерживаемых типов контента в извлеченных данных
        if data.get("_type") == "playlist":
            error_msg = "Плейлисты не поддерживаются"
            logger.warning(error_msg)
            self._last_result = RutubeResult(
                status="error",
                data=self._data,
                context=error_msg,
                code=RutubeErrorCode.PLAYLIST_NOT_SUPPORTED,
            )
            return self._last_result
        
        if data.get("is_live") == True:
            error_msg = "Прямые трансляции не поддерживаются"
            logger.warning(error_msg)
            self._last_result = RutubeResult(
                status="error",
                data=self._data,
                context=error_msg,
                code=RutubeErrorCode.LIVE_STREAM_NOT_SUPPORTED,
            )
            return self._last_result
        
        if data.get("media_type") == "livestream":
            error_msg = "Прямые эфиры не поддерживаются"
            logger.warning(error_msg)
            self._last_result = RutubeResult(
                status="error",
                data=self._data,
                context=error_msg,
                code=RutubeErrorCode.LIVE_STREAM_NOT_SUPPORTED,
            )
            return self._last_result

        # Заполнение объекта данных
        self._data.is_video = True
        self._data.title = data.get("title")
        self._data.author_name = data.get("uploader")
        self._data.description = data.get("description")
        
        # Извлечение видео форматов
        video_count = 0
        audio_count = 0
        for format in data.get("formats", []):
            if (
                format["ext"] == "mp4"
                and format["vcodec"] == "h264"
            ):
                self._data.videos.append(
                    RutubeVideo(
                        id=str(uuid4()),
                        url=format["url"],
                        name=format["format_id"],
                        has_audio=False if format["acodec"] == "none" else True,
                        fps=format.get("fps"),
                        width=format.get("width"),
                        height=format.get("height"),
                        language=format.get("language"),
                        total_bitrate=format.get("tbr"),
                        language_preference=format.get("language_preference"),
                    )
                )
                video_count += 1
                
            elif (
                format.get("ext") == "mp4" 
                and format.get("vcodec", "").startswith("avc1")
            ):
                self._data.videos.append(
                    RutubeVideo(
                        id=str(uuid4()),
                        url=format["url"],
                        name=format["format_id"],
                        has_audio=False if format["acodec"] == "none" else True,
                        fps=format.get("fps"),
                        width=format.get("width"),
                        height=format.get("height"),
                        language=format.get("language"),
                        total_bitrate=format.get("tbr"),
                        language_preference=format.get("language_preference"),
                    )
                )
                video_count += 1
                
            elif (
                format.get("ext") == "m4a"
                and format.get("vcodec") == "none"
                and format.get("acodec").startswith("mp4a")
            ):
                self._data.audios.append(
                    RutubeAudio(
                        id=str(uuid4()),
                        url=format["url"],
                        name=format["format_id"],
                        language=format.get("language"),
                        language_preference=format.get("language_preference"),
                    )
                )
                audio_count += 1
                
            elif (
                format.get("ext") == "m4a"
                and format.get("vcodec") == "none"
                and format.get("acodec").startswith("aac")
            ):
                self._data.audios.append(
                    RutubeAudio(
                        id=str(uuid4()),
                        url=format["url"],
                        name=format["format_id"],
                        language=format.get("language"),
                        language_preference=format.get("language_preference"),
                    )
                )
                audio_count += 1
                
        if audio_count == 0 and video_count == 0:
            error_msg = "Не найдено поддерживаемых медиа форматов"
            logger.warning(error_msg)
            self._last_result = RutubeResult(
                status="error",
                data=self._data,
                context=error_msg,
                code=RutubeErrorCode.NO_MEDIA_FORMATS_FOUND,
            )
            return self._last_result
        
        if video_count == 0:
            logger.warning("Не найдено видео форматов")
            
        if audio_count == 0:
            logger.warning("Не найдено аудио форматов")
                
        # Извлечение миниатюр
        thumbnail_count = 0
        for idx, thumbnail in enumerate(data.get("thumbnails", [])):  
            self._data.thumbnails.append(
                RutubeImage(
                    id=str(uuid4()),
                    url=thumbnail["url"],
                    name=f"Image_{idx}",
                    width=thumbnail.get("width"),
                    height=thumbnail.get("height"),
                )
            )
            thumbnail_count += 1
            
        if thumbnail_count == 0:
            logger.warning("Для видео не найдено миниатюр")
        
        logger.info(f"Извлечено {video_count} видео форматов, {audio_count} аудио форматов, {thumbnail_count} миниатюр")
        
        self._last_result = RutubeResult(data=self._data)
        return self._last_result

    def get_error_description(self, code: RutubeErrorCode) -> str:
        """
        Получение человеко-читаемого описания для кода ошибки.
        
        Args:
            code: Значение перечисления кода ошибки
            
        Returns:
            Строка с описанием
        """
        descriptions = {
            RutubeErrorCode.SUCCESS.value: "Операция успешно завершена",
            RutubeErrorCode.INVALID_URL.value: "Предоставленный URL Rutube невалиден или не поддерживается",
            RutubeErrorCode.EMPTY_URL.value: "Предоставлен пустой или невалидный URL",
            RutubeErrorCode.UNSUPPORTED_CONTENT_TYPE.value: "Тип контента Rutube не поддерживается",
            RutubeErrorCode.UNSUPPORTED_MEDIA_TYPE.value: "Тип медиа не поддерживается",
            RutubeErrorCode.CONNECTION_ERROR.value: "Произошла ошибка сетевого соединения",
            RutubeErrorCode.DOWNLOAD_ERROR.value: "Ошибка загрузки медиа",
            RutubeErrorCode.EXTRACTOR_ERROR.value: "Ошибка извлечения медиа",
            RutubeErrorCode.PROXY_ERROR.value: "Ошибка подключения к прокси",
            RutubeErrorCode.LIVE_STREAM_NOT_SUPPORTED.value: "Прямые трансляции не поддерживаются",
            RutubeErrorCode.PLAYLIST_NOT_SUPPORTED.value: "Плейлисты не поддерживаются",
            RutubeErrorCode.ACCOUNT_NOT_SUPPORTED.value: "Контент аккаунта/канала не поддерживается",
            RutubeErrorCode.NO_MEDIA_FORMATS_FOUND.value: "Не найдено поддерживаемых медиа форматов",
            RutubeErrorCode.NO_THUMBNAILS_FOUND.value: "Миниатюры не найдены",
            RutubeErrorCode.COOKIE_FILE_NOT_FOUND.value: "Файл cookie не найден",
            RutubeErrorCode.OUTPUT_PATH_ERROR.value: "Ошибка пути вывода",
            RutubeErrorCode.FILE_WRITE_ERROR.value: "Ошибка записи файла",
            RutubeErrorCode.UNEXPECTED_ERROR.value: "Произошла непредвиденная ошибка",
            RutubeErrorCode.INITIALIZATION_ERROR.value: "Ошибка инициализации загрузчика",
            RutubeErrorCode.EXTRACT_INFO_NOT_CALLED.value: "extract_info() должен быть вызван перед загрузкой",
            RutubeErrorCode.YT_DLP_ERROR.value: "Произошла внутренняя ошибка yt-dlp",
        }
        return descriptions.get(code, "Неизвестная ошибка")
