import logging
from datetime import datetime, timedelta, timezone

from .redis_base import RedisBase

logger = logging.getLogger("youtube_rate_limiter")


class YouTubeRateLimiter(RedisBase):
    """
    Лимитер скачиваний YouTube-видео.

    Хранит количество скачиваний YouTube-видео каждым пользователем за текущие сутки.
    Ключ создаётся с TTL до конца текущего дня (UTC+3, Москва).
    """

    DAILY_LIMIT = 3  # Максимум скачиваний YouTube-видео в день

    def __init__(self, host: str, port: int, db: int):
        super().__init__(host=host, port=port, db=db)
        logger.info(
            "YouTubeRateLimiter инициализирован: host=%s, port=%s, db=%s, limit=%d/день",
            host, port, db, self.DAILY_LIMIT,
        )

    def _get_key(self, chat_id: int) -> str:
        """Ключ вида yt_limit:{chat_id}:{YYYY-MM-DD}."""
        today = datetime.now(tz=timezone(timedelta(hours=3))).strftime("%Y-%m-%d")
        return f"yt_limit:{chat_id}:{today}"

    def _seconds_until_midnight(self) -> int:
        """Секунды до полуночи по МСК (UTC+3)."""
        msk = timezone(timedelta(hours=3))
        now = datetime.now(tz=msk)
        midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return max(int((midnight - now).total_seconds()), 1)

    def get_remaining(self, chat_id: int) -> int:
        """
        Сколько скачиваний осталось у пользователя сегодня.

        Returns:
            Количество оставшихся скачиваний (>= 0).
        """
        try:
            key = self._get_key(chat_id)
            count = self.redis_client.get(key)
            used = int(count) if count else 0
            return max(self.DAILY_LIMIT - used, 0)
        except Exception as e:
            logger.error("Ошибка get_remaining для chat_id=%s: %s", chat_id, e)
            return 0

    def can_download(self, chat_id: int) -> bool:
        """Может ли пользователь скачать ещё одно YouTube-видео сегодня."""
        return self.get_remaining(chat_id) > 0

    def increment(self, chat_id: int) -> int:
        """
        Увеличить счётчик скачиваний на 1.

        Returns:
            Новое значение счётчика.
        """
        try:
            key = self._get_key(chat_id)
            pipe = self.redis_client.pipeline()
            pipe.incr(key)
            pipe.expire(key, self._seconds_until_midnight())
            result = pipe.execute()
            new_count = result[0]
            logger.info(
                "YouTube download count для chat_id=%s: %s/%s",
                chat_id, new_count, self.DAILY_LIMIT,
            )
            return new_count
        except Exception as e:
            logger.error("Ошибка increment для chat_id=%s: %s", chat_id, e)
            return 0

    def get_used(self, chat_id: int) -> int:
        """Сколько уже скачано сегодня."""
        try:
            key = self._get_key(chat_id)
            count = self.redis_client.get(key)
            return int(count) if count else 0
        except Exception as e:
            logger.error("Ошибка get_used для chat_id=%s: %s", chat_id, e)
            return 0