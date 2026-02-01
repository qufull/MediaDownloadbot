import logging
import hashlib
from typing import Any, Dict, Optional

from .redis_base import RedisBase


# Создание логгера для этого модуля
logger = logging.getLogger("media_cache_storage")


class MediaCacheStorage(RedisBase):
    """
    Redis-хранилище для кэширования медиа-данных.
    
    Сохраняет информацию о медиа с ключами на основе URL и автоматическим истечением срока действия.
    Используется для кэширования метаданных медиа, чтобы избежать повторных вызовов API.
    """
    
    def __init__(self, host: str, port: int, db: int, ttl: int = 86400):
        """
        Инициализация хранилища кэша медиа.
        
        Args:
            host: Имя хоста Redis-сервера
            port: Порт Redis-сервера
            db: Номер базы данных Redis
            ttl: Время жизни записей кэша в секундах (по умолчанию: 86400 = 24 часа)
        """
        super().__init__(host=host, port=port, db=db)
        self.ttl = ttl
        logger.info("MediaCacheStorage инициализирован: host=%s, port=%s, db=%s, ttl=%ss", host, port, db, ttl)
    
    def _get_url_hash(self, url: str) -> str:
        """
        Генерация MD5-хэша URL для использования в качестве ключа кэша.
        
        Args:
            url: URL медиа
            
        Returns:
            Строка MD5-хэша URL
        """
        return hashlib.md5(url.encode()).hexdigest()
    
    def _get_cache_key(self, url: str) -> str:
        """
        Генерация ключа Redis для кэша медиа.
        
        Args:
            url: URL медиа
            
        Returns:
            Строка ключа Redis в формате 'media_cache:{url_hash}'
        """
        url_hash = self._get_url_hash(url=url)
        return f"media_cache:{url_hash}"
    
    def store_media(self, url: str, media_data: Dict[str, Any]) -> bool:
        """
        Сохранение медиа-данных в кэше с истечением срока действия TTL.
        
        Args:
            url: URL медиа
            media_data: Словарь с информацией о медиа
            
        Returns:
            True если данные успешно сохранены, False в противном случае
        """
        try:
            key = self._get_cache_key(url=url)
            
            cache_data = {
                "url": url,
                "data": media_data,
            }
            
            result = self.redis_client.setex(
                name=key,
                time=self.ttl,
                value=self._serialize(data=cache_data)
            )
            
            if result:
                logger.info("Медиа закэшировано для url=%s, ttl=%ss", url, self.ttl)
            else:
                logger.warning("Не удалось закэшировать медиа для url=%s", url)
                
            return result
            
        except Exception as e:
            logger.error("Ошибка сохранения кэша медиа для url=%s: %s", url, e)
            return False
    
    def get_media(self, url: str) -> Optional[Dict[str, Any]]:
        """
        Получение медиа-данных из кэша с обновлением TTL.
        
        Args:
            url: URL медиа
            
        Returns:
            Словарь закэшированных медиа-данных или None, если не найдено
        """
        try:
            key = self._get_cache_key(url=url)
            data = self.redis_client.get(name=key)
            
            if data:
                cache_data = self._deserialize(data=data)
                
                self.redis_client.setex(
                    name=key,
                    time=self.ttl,
                    value=self._serialize(data=cache_data)
                )
                
                logger.debug("Кэш медиа получен для url=%s, TTL обновлен", url)
                return cache_data
                
            logger.debug("Кэш медиа не найден для url=%s", url)
            return None
            
        except Exception as e:
            logger.error("Ошибка получения кэша медиа для url=%s: %s", url, e)
            return None
