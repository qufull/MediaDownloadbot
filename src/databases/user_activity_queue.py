import json
import logging
from typing import Any, Dict, List, Optional

from .redis_base import RedisBase

logger = logging.getLogger("user_activity_queue")

MAX_QUEUE_SIZE = 3          # максимум ссылок в очереди одного пользователя
QUEUE_TTL      = 600        # TTL всего списка (сек) — сбрасывается при каждом push/pop


class UserActivityQueue(RedisBase):
    """
    Очередь пользовательской активности.

    Для задач extract используется Redis-список (LPUSH / RPOP):
      - ключ  : user_queue:{chat_id}
      - каждый элемент — JSON {url, service}
      - максимум MAX_QUEUE_SIZE элементов

    Для задач download используется старый подход (один ключ setex),
    т.к. скачивание — разовая тяжёлая операция после выбора качества.
    """

    def __init__(self, host: str, port: int, db: int, ttl: int = QUEUE_TTL):
        super().__init__(host=host, port=port, db=db)
        self.ttl = ttl
        logger.info(
            "UserActivityQueue инициализирован: host=%s port=%s db=%s ttl=%ss max_queue=%s",
            host, port, db, ttl, MAX_QUEUE_SIZE,
        )

    def _queue_key(self, chat_id: int) -> str:
        return f"user_queue:{chat_id}"

    def _processing_key(self, chat_id: int) -> str:
        return f"user_processing:{chat_id}"

    def _download_key(self, chat_id: int) -> str:
        return f"user_download:{chat_id}"

    # ── Очередь ссылок ────────────────────────────────────────────────

    def queue_size(self, chat_id: int) -> int:
        try:
            return self.redis_client.llen(self._queue_key(chat_id))
        except Exception as e:
            logger.error("queue_size error chat_id=%s: %s", chat_id, e)
            return 0

    def push_url(self, chat_id: int, url: str, service: str) -> bool:
        try:
            key = self._queue_key(chat_id)
            if self.redis_client.llen(key) >= MAX_QUEUE_SIZE:
                logger.warning("Очередь переполнена для chat_id=%s (max=%s)", chat_id, MAX_QUEUE_SIZE)
                return False
            item = self._serialize({"url": url, "service": service})
            self.redis_client.rpush(key, item)
            self.redis_client.expire(key, self.ttl)
            size = self.redis_client.llen(key)
            logger.info("URL добавлен в очередь chat_id=%s, позиция=%s, url=%s", chat_id, size, url)
            return True
        except Exception as e:
            logger.error("push_url error chat_id=%s: %s", chat_id, e)
            return False

    def pop_url(self, chat_id: int) -> Optional[Dict[str, Any]]:
        try:
            key = self._queue_key(chat_id)
            raw = self.redis_client.lpop(key)
            if raw is None:
                return None
            if self.redis_client.llen(key) > 0:
                self.redis_client.expire(key, self.ttl)
            return self._deserialize(raw)
        except Exception as e:
            logger.error("pop_url error chat_id=%s: %s", chat_id, e)
            return None

    def peek_queue(self, chat_id: int) -> List[Dict[str, Any]]:
        try:
            raw_list = self.redis_client.lrange(self._queue_key(chat_id), 0, -1)
            return [self._deserialize(r) for r in raw_list]
        except Exception as e:
            logger.error("peek_queue error chat_id=%s: %s", chat_id, e)
            return []

    def clear_queue(self, chat_id: int) -> bool:
        try:
            self.redis_client.delete(self._queue_key(chat_id))
            return True
        except Exception as e:
            logger.error("clear_queue error chat_id=%s: %s", chat_id, e)
            return False

    # ── Флаг «в обработке» ───────────────────────────────────────────

    def set_processing(self, chat_id: int, url: str, service: str) -> bool:
        try:
            key = self._processing_key(chat_id)
            data = self._serialize({"url": url, "service": service})
            self.redis_client.setex(key, self.ttl, data)
            logger.debug("Processing flag set for chat_id=%s", chat_id)
            return True
        except Exception as e:
            logger.error("set_processing error chat_id=%s: %s", chat_id, e)
            return False

    def is_processing(self, chat_id: int) -> bool:
        try:
            return self.redis_client.exists(self._processing_key(chat_id)) == 1
        except Exception as e:
            logger.error("is_processing error chat_id=%s: %s", chat_id, e)
            return False

    def clear_processing(self, chat_id: int) -> bool:
        try:
            self.redis_client.delete(self._processing_key(chat_id))
            logger.debug("Processing flag cleared for chat_id=%s", chat_id)
            return True
        except Exception as e:
            logger.error("clear_processing error chat_id=%s: %s", chat_id, e)
            return False

    # ── Обратная совместимость ────────────────────────────────────────

    def create_extract(self, chat_id: int, url: str, service: str) -> bool:
        return self.set_processing(chat_id=chat_id, url=url, service=service)

    def get_extract(self, chat_id: int) -> Optional[Dict[str, Any]]:
        try:
            raw = self.redis_client.get(self._processing_key(chat_id))
            return self._deserialize(raw) if raw else None
        except Exception as e:
            logger.error("get_extract error chat_id=%s: %s", chat_id, e)
            return None

    def delete_extract(self, chat_id: int) -> bool:
        return self.clear_processing(chat_id=chat_id)

    # ── Download (без изменений) ──────────────────────────────────────

    def create_download(self, chat_id: int, url: str, service: str) -> bool:
        try:
            key = self._download_key(chat_id)
            data = self._serialize({"url": url, "service": service})
            result = self.redis_client.setex(key, self.ttl, data)
            logger.info("Download task created for chat_id=%s service=%s", chat_id, service)
            return bool(result)
        except Exception as e:
            logger.error("create_download error chat_id=%s: %s", chat_id, e)
            return False

    def get_download(self, chat_id: int) -> Optional[Dict[str, Any]]:
        try:
            raw = self.redis_client.get(self._download_key(chat_id))
            return self._deserialize(raw) if raw else None
        except Exception as e:
            logger.error("get_download error chat_id=%s: %s", chat_id, e)
            return None

    def delete_download(self, chat_id: int) -> bool:
        try:
            result = self.redis_client.delete(self._download_key(chat_id))
            return bool(result)
        except Exception as e:
            logger.error("delete_download error chat_id=%s: %s", chat_id, e)
            return False

    # ── Системные ─────────────────────────────────────────────────────

    def clear_all(self) -> bool:
        try:
            self.redis_client.flushdb()
            logger.warning("Все данные очереди (db=%s) очищены!", self.db)
            return True
        except Exception as e:
            logger.error("clear_all error: %s", e)
            return False