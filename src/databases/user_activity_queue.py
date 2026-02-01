import logging
from typing import Any, Dict, Optional

from .redis_base import RedisBase


# Создание логгера для этого модуля
logger = logging.getLogger("user_activity_queue")


class UserActivityQueue(RedisBase):
    """
    Очередь пользовательской активности для управления задачами извлечения и загрузки.
    
    Предоставляет методы для отслеживания текущих задач пользователей:
    - Извлечение информации о медиа (extract)
    - Загрузка медиа-контента (download)
    
    Каждая задача хранится с TTL для автоматической очистки неактивных сессий.
    """
    
    def __init__(self, host: str, port: int, db: int, ttl: int = 7200):
        """
        Инициализация очереди пользовательской активности.
        
        Args:
            host: Имя хоста Redis-сервера
            port: Порт Redis-сервера
            db: Номер базы данных Redis
            ttl: Время жизни задач в секундах (по умолчанию: 7200 = 2 часа)
        """
        super().__init__(host=host, port=port, db=db)
        self.ttl = ttl
        logger.info("UserActivityQueue инициализирован: host=%s, port=%s, db=%s, ttl=%ss", host, port, db, ttl)
      
    def _get_extract_queue_key(self, chat_id: int) -> str:
        """
        Генерация ключа Redis для задач извлечения информации.
        
        Args:
            chat_id: ID чата Telegram
            
        Returns:
            Строка ключа Redis в формате 'user_extract:{chat_id}'
        """
        return f"user_extract:{chat_id}"
    
    def _get_download_queue_key(self, chat_id: int) -> str:
        """
        Генерация ключа Redis для задач загрузки.
        
        Args:
            chat_id: ID чата Telegram
            
        Returns:
            Строка ключа Redis в формате 'user_download:{chat_id}'
        """
        return f"user_download:{chat_id}"
    
    def create_extract(self, chat_id: int, url: str, service: str) -> bool:
        """
        Создание задачи извлечения информации о медиа.
        
        Args:
            chat_id: ID чата Telegram
            url: URL медиа для извлечения информации
            service: Название сервиса (youtube, tiktok, и т.д.)
            
        Returns:
            True если задача создана успешно, False в противном случае
        """
        try:
            key = self._get_extract_queue_key(chat_id=chat_id)
            
            session_data = {
                "url": url,
                "service": service,
            }
            
            result = self.redis_client.setex(
                name=key, 
                time=self.ttl,
                value=self._serialize(session_data)
            )
            
            if result:
                logger.info("Задача извлечения создана для chat_id=%s, service=%s, ttl=%ss", chat_id, service, self.ttl)
            else:
                logger.warning("Не удалось создать задачу извлечения для chat_id=%s", chat_id)
                
            return result
            
        except Exception as e:
            logger.error("Ошибка создания задачи извлечения для chat_id=%s: %s", chat_id, e)
            return False
        
    def create_download(self, chat_id: int, url: str, service: str) -> bool:
        """
        Создание задачи загрузки медиа-контента.
        
        Args:
            chat_id: ID чата Telegram
            url: URL медиа для загрузки
            service: Название сервиса (youtube, tiktok, и т.д.)
            
        Returns:
            True если задача создана успешно, False в противном случае
        """
        try:
            key = self._get_download_queue_key(chat_id=chat_id)
            
            session_data = {
                "url": url,
                "service": service,
            }
            
            result = self.redis_client.setex(
                name=key, 
                time=self.ttl,
                value=self._serialize(session_data)
            )
            
            if result:
                logger.info("Задача загрузки создана для chat_id=%s, service=%s, ttl=%ss", chat_id, service, self.ttl)
            else:
                logger.warning("Не удалось создать задачу загрузки для chat_id=%s", chat_id)
                
            return result
            
        except Exception as e:
            logger.error("Ошибка создания задачи загрузки для chat_id=%s: %s", chat_id, e)
            return False
    
    def get_extract(self, chat_id: int) -> Optional[Dict[str, Any]]:
        """
        Получение задачи извлечения информации с обновлением TTL.
        
        Args:
            chat_id: ID чата Telegram
            
        Returns:
            Данные задачи извлечения или None если задача не найдена
        """
        try:
            key = self._get_extract_queue_key(chat_id=chat_id)
            data = self.redis_client.get(name=key)
            
            if data:
                session_data = self._deserialize(data=data)
                self.redis_client.setex(name=key, time=self.ttl, value=self._serialize(session_data))
                logger.debug("Задача извлечения получена для chat_id=%s, TTL обновлен", chat_id)
                return session_data
                
            logger.debug("Задача извлечения не найдена для chat_id=%s", chat_id)
            return None
            
        except Exception as e:
            logger.error("Ошибка получения задачи извлечения для chat_id=%s: %s", chat_id, e)
            return None
        
    def get_download(self, chat_id: int) -> Optional[Dict[str, Any]]:
        """
        Получение задачи загрузки с обновлением TTL.
        
        Args:
            chat_id: ID чата Telegram
            
        Returns:
            Данные задачи загрузки или None если задача не найдена
        """
        try:
            key = self._get_download_queue_key(chat_id=chat_id)
            data = self.redis_client.get(name=key)
            
            if data:
                session_data = self._deserialize(data=data)
                self.redis_client.setex(name=key, time=self.ttl, value=self._serialize(session_data))
                logger.debug("Задача загрузки получена для chat_id=%s, TTL обновлен", chat_id)
                return session_data
                
            logger.debug("Задача загрузки не найдена для chat_id=%s", chat_id)
            return None
            
        except Exception as e:
            logger.error("Ошибка получения задачи загрузки для chat_id=%s: %s", chat_id, e)
            return None
        
    def delete_extract(self, chat_id: int) -> bool:
        """
        Удаление задачи извлечения информации.
        
        Args:
            chat_id: ID чата Telegram
            
        Returns:
            True если задача удалена успешно, False в противном случае
        """
        try:
            key = self._get_extract_queue_key(chat_id=chat_id)
            result = self.redis_client.delete(key)
            
            if result:
                logger.info("Задача извлечения удалена для chat_id=%s", chat_id)
            else:
                logger.debug("Задача извлечения не найдена для удаления chat_id=%s", chat_id)
                
            return bool(result)
            
        except Exception as e:
            logger.error("Ошибка удаления задачи извлечения для chat_id=%s: %s", chat_id, e)
            return False
        
    def delete_download(self, chat_id: int) -> bool:
        """
        Удаление задачи загрузки.
        
        Args:
            chat_id: ID чата Telegram
            
        Returns:
            True если задача удалена успешно, False в противном случае
        """
        try:
            key = self._get_download_queue_key(chat_id=chat_id)
            result = self.redis_client.delete(key)
            
            if result:
                logger.info("Задача загрузки удалена для chat_id=%s", chat_id)
            else:
                logger.debug("Задача загрузки не найдена для удаления chat_id=%s", chat_id)
                
            return bool(result)
            
        except Exception as e:
            logger.error("Ошибка удаления задачи загрузки для chat_id=%s: %s", chat_id, e)
            return False
        
    def clear_all(self) -> bool:
        """
        Полная очистка базы данных Redis для текущей очереди.

        Удаляет все ключи в выбранной базе (db), включая все задачи извлечения и загрузки.

        Returns:
            True — если очистка выполнена успешно
            False — если произошла ошибка
        """
        try:
            self.redis_client.flushdb()
            logger.warning("Все данные очереди (db=%s) были полностью очищены!", self.redis_client.connection_pool.connection_kwargs.get('db'))
            return True

        except Exception as e:
            logger.error("Ошибка при очистке базы Redis (db=%s): %s", self.redis_client.connection_pool.connection_kwargs.get('db'), e)
            return False
