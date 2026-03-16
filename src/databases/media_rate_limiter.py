import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from .redis_base import RedisBase
from .user_registry import UserRegistry

logger = logging.getLogger("media_rate_limiter")


class MediaRateLimiter(RedisBase):
    """
    Универсальный лимитер скачиваний для всех сервисов.
    """

    DAILY_LIMIT = 3  # Базовый лимит скачиваний в сутки
    MAX_HARD_LIMIT = 10  # Максимальный лимит с бонусами для обычных юзеров
    PREMIUM_YOUTUBE_LIMIT = 30  # Лимит на YouTube для Premium пользователей

    def __init__(self, host: str, port: int, db: int, user_registry: UserRegistry):
        super().__init__(host=host, port=port, db=db)
        self.user_registry = user_registry
        logger.info(
            "MediaRateLimiter инициализирован: host=%s, port=%s, db=%s, base_limit=%d/день",
            host, port, db, self.DAILY_LIMIT,
        )

    def _get_key(self, chat_id: int) -> str:
        """Общий ключ для обычных лимитов."""
        today = datetime.now(tz=timezone(timedelta(hours=3))).strftime("%Y-%m-%d")
        return f"media_limit:{chat_id}:{today}"

    def _get_yt_key(self, chat_id: int) -> str:
        """Отдельный ключ для подсчета загрузок с YouTube."""
        today = datetime.now(tz=timezone(timedelta(hours=3))).strftime("%Y-%m-%d")
        return f"media_limit:yt:{chat_id}:{today}"

    def _seconds_until_midnight(self) -> int:
        msk = timezone(timedelta(hours=3))
        now = datetime.now(tz=msk)
        midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return max(int((midnight - now).total_seconds()), 1)

    def get_max_allowed(self, chat_id: int) -> int:
        """Максимально допустимое кол-во для Обычного пользователя (с бонусами)."""
        try:
            bonus = self.user_registry.get_bonus(chat_id)
            total = self.DAILY_LIMIT + bonus
            return min(total, self.MAX_HARD_LIMIT)
        except Exception as e:
            logger.error(f"Ошибка получения бонуса для {chat_id}: {e}")
            return self.DAILY_LIMIT

    def get_used(self, chat_id: int) -> int:
        """Сколько скачиваний (любых) потрачено сегодня."""
        try:
            count = self.redis_client.get(self._get_key(chat_id))
            return int(count) if count else 0
        except Exception:
            return 0

    def get_youtube_used(self, chat_id: int) -> int:
        """Сколько скачиваний именно с YouTube потрачено сегодня."""
        try:
            count = self.redis_client.get(self._get_yt_key(chat_id))
            return int(count) if count else 0
        except Exception:
            return 0

    def get_remaining(self, chat_id: int) -> int:
        """Сколько скачиваний осталось у обычного пользователя."""
        max_allowed = self.get_max_allowed(chat_id)
        used = self.get_used(chat_id)
        return max(max_allowed - used, 0)

    def can_download(self, chat_id: int, service: Optional[str] = None) -> bool:
        """Проверка: может ли пользователь скачать медиа прямо сейчас."""
        # 1. Проверяем Premium
        if self.user_registry.is_user_premium(chat_id):
            if service == "youtube":
                # Для Ютуба жесткий лимит даже с премиумом
                return self.get_youtube_used(chat_id) < self.PREMIUM_YOUTUBE_LIMIT
            # Для остальных сервисов (TikTok, IG) - безлимит
            return True

        # 2. Проверяем обычного пользователя
        return self.get_remaining(chat_id) > 0

    def increment(self, chat_id: int, service: Optional[str] = None) -> int:
        """
        Увеличить счетчик скачиваний в Redis на 1.
        Вызывается ПОСЛЕ успешной отправки видео.
        """
        try:
            pipe = self.redis_client.pipeline()
            ttl = self._seconds_until_midnight()

            # Увеличиваем общий счетчик
            global_key = self._get_key(chat_id)
            pipe.incr(global_key)
            pipe.expire(global_key, ttl)

            # Если качаем YouTube, параллельно увеличиваем отдельный счетчик
            if service == "youtube":
                yt_key = self._get_yt_key(chat_id)
                pipe.incr(yt_key)
                pipe.expire(yt_key, ttl)

            result = pipe.execute()
            new_count = result[0] # значение общего счетчика

            logger.info("User %s usage incremented. Service: %s, Total: %s", chat_id, service, new_count)
            return new_count
        except Exception as e:
            logger.error(f"Ошибка increment для {chat_id}: {e}")
            return 0