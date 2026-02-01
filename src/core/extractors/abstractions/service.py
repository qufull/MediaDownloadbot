from abc import ABC, abstractmethod

from .models import AbstractResultModel


class AbstractExtractor(ABC):
    """
    Базовый абстрактный класс для загрузчиков из сервисов.
    
    Определяет интерфейс, который должны реализовать единый способ извлечения медиа-данных
    """
    
    @abstractmethod
    def extract_info(self, url: str) -> AbstractResultModel:
        """
        Извлекает информацию о медиа по URL без загрузки файлов.
        
        Аргументы:
            url: Ссылка для извлечения информации
            
        Возвращает:
            AbstractServiceResult с собранными данными
        """
        raise NotImplementedError()
