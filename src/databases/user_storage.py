import logging
from typing import Any, Dict, Optional

from .redis_base import RedisBase


# Создание логгера для этого модуля
logger = logging.getLogger("user_session_storage")


class UserSessionStorage(RedisBase):
    """
    Redis-хранилище для пользовательских сессий с настраиваемым TTL.
    
    Предоставляет методы для создания, получения и управления пользовательскими сессиями
    с автоматическим истечением срока действия. Каждая сессия сохраняет информацию о медиа
    и служебные данные для быстрого доступа.
    """
    
    def __init__(self, host: str, port: int, db: int, ttl: int = 7200):
        """
        Инициализация хранилища пользовательских сессий.
        
        Args:
            host: Имя хоста Redis-сервера
            port: Порт Redis-сервера
            db: Номер базы данных Redis
            ttl: Время жизни сессий в секундах (по умолчанию: 7200 = 2 часа)
        """
        super().__init__(host=host, port=port, db=db)
        self.ttl = ttl
        logger.info("UserSessionStorage инициализирован: host=%s, port=%s, db=%s, ttl=%ss", host, port, db, ttl)
            
    def _get_session_key(self, chat_id: int) -> str:
        """
        Генерация ключа Redis для пользовательской сессии.
        
        Args:
            chat_id: ID чата Telegram
            
        Returns:
            Строка ключа Redis в формате 'user_session:{chat_id}'
        """
        return f"user_session:{chat_id}"
    
    def create_session(self, chat_id: int, url: str, service: str, media_data: Dict[str, Any]) -> bool:
        """
        Создание новой пользовательской сессии с истечением срока действия TTL.
        
        Args:
            chat_id: ID чата Telegram
            url: URL медиа, запрошенный пользователем
            service: Название сервиса (youtube, instagram, и т.д.)
            media_data: Информация о медиа, включая форматы, миниатюры и т.д.
            
        Returns:
            True если сессия создана успешно, False в противном случае
        """
        try:
            key = self._get_session_key(chat_id=chat_id)
            
            session_data = {
                "url": url,
                "service": service,
                "media_data": media_data,
            }
            
            result = self.redis_client.setex(
                name=key, 
                time=self.ttl,
                value=self._serialize(session_data)
            )
            
            if result:
                logger.info("Сессия создана для chat_id=%s, service=%s, ttl=%ss", chat_id, service, self.ttl)
            else:
                logger.warning("Не удалось создать сессию для chat_id=%s", chat_id)
                
            return result
            
        except Exception as e:
            logger.error("Ошибка создания сессии для chat_id=%s: %s", chat_id, e)
            return False
    
    def get_session(self, chat_id: int) -> Optional[Dict[str, Any]]:
        """
        Получение пользовательской сессии с обновлением TTL.
        
        При доступе к сессии её TTL сбрасывается, чтобы поддерживать
        сессию активной для активных пользователей.
        
        Args:
            chat_id: ID чата Telegram
            
        Returns:
            Словарь данных сессии или None если сессия не существует
        """
        try:
            key = self._get_session_key(chat_id=chat_id)
            data = self.redis_client.get(name=key)
            
            if data:
                session_data = self._deserialize(data=data)
                self.redis_client.setex(name=key, time=self.ttl, value=self._serialize(session_data))
                logger.debug("Сессия получена для chat_id=%s, TTL обновлен", chat_id)
                return session_data
                
            logger.debug("Сессия не найдена для chat_id=%s", chat_id)
            return None
            
        except Exception as e:
            logger.error("Ошибка получения сессии для chat_id=%s: %s", chat_id, e)
            return None
