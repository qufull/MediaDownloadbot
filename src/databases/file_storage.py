# src/storage/file_id_cache.py
import logging
import hashlib
from typing import Optional

from .redis_base import RedisBase

logger = logging.getLogger("file_id_cache")


class FileIdCache(RedisBase):
    """
    Redis-хранилище для кэширования file_id Telegram.

    Позволяет повторно отправлять видео без скачивания,
    используя file_id из предыдущих отправок.

    Ключ кэша: file_cache:{url_hash}:{height}
    """

    def __init__(self, host: str, port: int, db: int, ttl: int = 604800):
        """
        Инициализация кэша file_id.

        Args:
            host: Хост Redis
            port: Порт Redis
            db: Номер БД Redis
            ttl: Время жизни записи (по умолчанию 7 дней = 604800 сек)
        """
        super().__init__(host=host, port=port, db=db)
        self.ttl = ttl
        logger.info("FileIdCache инициализирован: host=%s, port=%s, db=%s, ttl=%ss", host, port, db, ttl)

    def _get_url_hash(self, url: str) -> str:
        """MD5 хэш URL для ключа."""
        return hashlib.md5(url.encode()).hexdigest()

    def _get_cache_key(self, url: str, height: int) -> str:
        """
        Генерация ключа кэша.

        Args:
            url: URL видео
            height: Высота видео (качество)

        Returns:
            Ключ в формате 'file_cache:{url_hash}:{height}'
        """
        url_hash = self._get_url_hash(url)
        return f"file_cache:{url_hash}:{height}"

    def store_file_id(self, url: str, height: int, file_id: str, width: int = 0) -> bool:
        """
        Сохранить file_id в кэш.

        Args:
            url: URL видео
            height: Высота видео
            file_id: Telegram file_id
            width: Ширина видео (опционально)

        Returns:
            True если успешно сохранено
        """
        try:
            key = self._get_cache_key(url, height)

            data = {
                "file_id": file_id,
                "height": height,
                "width": width,
            }

            result = self.redis_client.setex(
                name=key,
                time=self.ttl,
                value=self._serialize(data)
            )

            if result:
                logger.info("🚀 file_id закэширован: url_hash=%s, height=%sp", self._get_url_hash(url)[:8], height)
            else:
                logger.warning("Не удалось закэшировать file_id для height=%sp", height)

            return bool(result)

        except Exception as e:
            logger.error("Ошибка сохранения file_id: %s", e)
            return False

    def get_file_id(self, url: str, height: int) -> Optional[str]:
        """
        Получить file_id из кэша.

        Args:
            url: URL видео
            height: Высота видео

        Returns:
            file_id или None
        """
        try:
            key = self._get_cache_key(url, height)
            data = self.redis_client.get(key)

            if data:
                cache_data = self._deserialize(data)
                # Обновляем TTL при доступе
                self.redis_client.expire(key, self.ttl)
                logger.debug("🚀 file_id найден в кэше: height=%sp", height)
                return cache_data.get("file_id")

            return None

        except Exception as e:
            logger.error("Ошибка получения file_id: %s", e)
            return None

    def get_cached_qualities(self, url: str) -> set:
        """
        Получить список закэшированных качеств для URL.

        Args:
            url: URL видео

        Returns:
            Set высот (качеств) которые есть в кэше
        """
        try:
            url_hash = self._get_url_hash(url)
            pattern = f"file_cache:{url_hash}:*"

            cached_heights = set()

            for key in self.redis_client.scan_iter(match=pattern):
                # Извлекаем height из ключа file_cache:{hash}:{height}
                try:
                    key_str = key.decode() if isinstance(key, bytes) else key
                    height = int(key_str.split(":")[-1])
                    cached_heights.add(height)
                except (ValueError, IndexError):
                    continue

            if cached_heights:
                logger.debug("Найдены закэшированные качества для url: %s", cached_heights)

            return cached_heights

        except Exception as e:
            logger.error("Ошибка получения закэшированных качеств: %s", e)
            return set()

    def delete_cached(self, url: str, height: int) -> bool:
        """Удалить file_id из кэша."""
        try:
            key = self._get_cache_key(url, height)
            result = self.redis_client.delete(key)
            if result:
                logger.info("file_id удален из кэша: height=%sp", height)
            return bool(result)
        except Exception as e:
            logger.error("Ошибка удаления file_id: %s", e)
            return False