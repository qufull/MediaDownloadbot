import json
import redis
from typing import Any, Optional


class RedisBase:
    """
    Базовый класс для реализаций Redis-хранилищ.
    
    Предоставляет общие методы сериализации/десериализации и управления подключением
    для классов хранилищ на основе Redis. Обрабатывает JSON-сериализацию и тестирование
    подключения для производных классов хранилищ.
    
    Атрибуты:
        redis_client (redis.Redis): Экземпляр Redis-клиента
        host (str): Имя хоста Redis-сервера
        port (int): Порт Redis-сервера
        db (int): Номер базы данных Redis
        
    Пример:
        >>> from src.storage.redis_base import RedisBase
        >>> 
        >>> class UserSessionStorage(RedisBase):
        ...     def __init__(self):
        ...         super().__init__(
        ...             host='localhost',
        ...             port=6379,
        ...             db=0
        ...         )
    """
    
    def __init__(self, host: str, port: int, db: int) -> None:
        """
        Инициализация Redis-клиента с параметрами подключения.
        
        Args:
            host: Имя хоста или IP-адрес Redis-сервера
            port: Порт Redis-сервера
            db: Номер базы данных Redis (0-15)
            
        Raises:
            redis.ConnectionError: Если подключение к Redis-серверу не удалось
            
        Пример:
            >>> storage = RedisBase(host='localhost', port=6379, db=0)
            >>> if storage.ping():
            ...     print("Подключение успешно")
        """
        self.redis_client = redis.Redis(
            host=host,
            port=port,
            db=db,
            decode_responses=True,
            encoding='utf-8'
        )
        self.host = host
        self.port = port
        self.db = db
    
    def _serialize(self, data: Any) -> str:
        """
        Сериализация Python-объекта в JSON-строку.
        
        Преобразует Python-объекты в JSON-формат для хранения в Redis.
        Обрабатывает несериализуемые объекты, преобразуя их в строки.
        
        Args:
            data: Python-объект для сериализации (dict, list, str, int, и т.д.)
            
        Returns:
            JSON-форматированное строковое представление данных
            
        Пример:
            >>> data = {'user_id': 123, 'media_url': 'https://example.com/video'}
            >>> serialized = storage._serialize(data)
            >>> print(serialized)
            '{"user_id": 123, "media_url": "https://example.com/video"}'
        """
        return json.dumps(data, ensure_ascii=False, default=str)
    
    def _deserialize(self, data: str) -> Optional[Any]:
        """
        Десериализация JSON-строки в Python-объект.
        
        Преобразует JSON-строку из Redis обратно в Python-объект.
        
        Args:
            data: JSON-строка для десериализации
            
        Returns:
            Десериализованный Python-объект или None если входные данные пусты
            
        Raises:
            json.JSONDecodeError: Если входная строка не является валидным JSON
            
        Пример:
            >>> json_string = '{"user_id": 123, "media_url": "https://example.com/video"}'
            >>> deserialized = storage._deserialize(json_string)
            >>> print(deserialized)
            {'user_id': 123, 'media_url': 'https://example.com/video'}
        """
        if data:
            return json.loads(data)
        return None
    
    def ping(self) -> bool:
        """
        Тестирование подключения к Redis-серверу.
        
        Отправляет команду PING на Redis-сервер для проверки подключения.
        
        Returns:
            True если подключение успешно, False если подключение не удалось
            
        Пример:
            >>> if storage.ping():
            ...     print("Подключение к Redis: OK")
            ... else:
            ...     print("Подключение к Redis: ОШИБКА")
        """
        try:
            return self.redis_client.ping()
        except redis.ConnectionError:
            return False
    
    def get_connection_info(self) -> dict:
        """
        Получение параметров подключения и статуса Redis.
        
        Returns:
            Словарь, содержащий детали подключения и статус
            
        Пример:
            >>> info = storage.get_connection_info()
            >>> print(f"Хост: {info['host']}:{info['port']}")
            >>> print(f"База данных: {info['db']}")
            >>> print(f"Статус: {info['status']}")
        """
        return {
            'host': self.host,
            'port': self.port,
            'db': self.db,
            'status': 'connected' if self.ping() else 'disconnected'
        }
