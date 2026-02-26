import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from .redis_base import RedisBase
from .user_registry import UserRegistry

logger = logging.getLogger("media_rate_limiter")


class MediaRateLimiter(RedisBase):
    """
    Универсальный лимитер скачиваний для всех сервисов.

    Расчет лимита:
    Остаток = (Базовый лимит + Бонусы из SQLite) - Текущее использование из Redis.
    """

    DAILY_LIMIT = 3  # Базовый лимит скачиваний в сутки
    MAX_HARD_LIMIT = 10

    def __init__(self, host: str, port: int, db: int, user_registry: UserRegistry):
        """
        Инициализация.
        :param user_registry: Экземпляр UserRegistry для получения бонусов из SQLite.
        """
        super().__init__(host=host, port=port, db=db)
        self.user_registry = user_registry
        logger.info(
            "MediaRateLimiter инициализирован: host=%s, port=%s, db=%s, base_limit=%d/день",
            host, port, db, self.DAILY_LIMIT,
        )

    def _get_key(self, chat_id: int) -> str:
        """Ключ вида media_limit:{chat_id}:{YYYY-MM-DD}."""
        # Используем МСК (UTC+3)
        today = datetime.now(tz=timezone(timedelta(hours=3))).strftime("%Y-%m-%d")
        return f"media_limit:{chat_id}:{today}"

    def _seconds_until_midnight(self) -> int:
        """Секунды до полуночи по МСК (UTC+3) для TTL ключа в Redis."""
        msk = timezone(timedelta(hours=3))
        now = datetime.now(tz=msk)
        midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return max(int((midnight - now).total_seconds()), 1)

    def get_max_allowed(self, chat_id: int) -> int:
        """
        Вычисляет максимально допустимое кол-во скачиваний:
        Базовый лимит + накопленные бонусы из SQLite.
        """
        try:
            bonus = self.user_registry.get_bonus(chat_id)
            total = self.DAILY_LIMIT + bonus

            return min(total, self.MAX_HARD_LIMIT)

        except Exception as e:
            logger.error(f"Ошибка получения бонуса для {chat_id}: {e}")
            return self.DAILY_LIMIT

    def get_used(self, chat_id: int) -> int:
        """Сколько скачиваний уже потрачено сегодня (из Redis)."""
        try:
            key = self._get_key(chat_id)
            count = self.redis_client.get(key)
            return int(count) if count else 0
        except Exception as e:
            logger.error(f"Ошибка get_used для {chat_id}: {e}")
            return 0

    def get_remaining(self, chat_id: int) -> int:
        """Сколько скачиваний осталось у пользователя на сегодня."""
        max_allowed = self.get_max_allowed(chat_id)
        used = self.get_used(chat_id)
        return max(max_allowed - used, 0)

    def can_download(self, chat_id: int) -> bool:
        """Проверка: может ли пользователь скачать медиа прямо сейчас."""
        return self.get_remaining(chat_id) > 0

    def increment(self, chat_id: int) -> int:
        """
        Увеличить счетчик скачиваний в Redis на 1.
        Вызывается ПОСЛЕ успешной отправки видео пользователю.
        """
        try:
            key = self._get_key(chat_id)
            pipe = self.redis_client.pipeline()
            pipe.incr(key)
            # Ставим TTL, чтобы ключ сам удалился в конце дня
            pipe.expire(key, self._seconds_until_midnight())
            result = pipe.execute()

            new_count = result[0]
            max_allowed = self.get_max_allowed(chat_id)

            logger.info(
                "User %s usage: %s/%s",
                chat_id, new_count, max_allowed,
            )
            return new_count
        except Exception as e:
            logger.error(f"Ошибка increment для {chat_id}: {e}")
            return 0