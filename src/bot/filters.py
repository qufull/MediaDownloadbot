from urllib.parse import urlparse

from aiogram.types import Message
from aiogram.filters import BaseFilter

from .patterns import DomainMatcher


class URLFilter(BaseFilter):
    """Фильтр для проверки сообщений на наличие валидных URL-адресов."""
    
    def __init__(self, check_support: bool = True):
        """Инициализирует фильтр URL."""
        self.check_support = check_support
    
    async def __call__(self, message: Message) -> bool:
        """Вызывается при проверке сообщения фильтром."""
        text = message.text or message.caption or ""
        if not text.strip():
            return False
        
        return self.is_valid_url(text)
    
    def is_valid_url(self, text: str) -> bool:
        """Проверяет валидность URL и поддержку домена."""
        try:
            parsed = urlparse(text.strip())
            if not (parsed.scheme in ('http', 'https') and parsed.netloc):
                return False
            
            domain = parsed.netloc.lower()
            
            # Если требуется проверка поддержки домена
            if self.check_support:
                return DomainMatcher.is_domain_supported(domain)
            
            return True
            
        except Exception:
            return False
