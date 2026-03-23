from typing import Dict, List, Optional
from enum import Enum

from .common import ServiceType


class DomainMatchStrategy(Enum):
    """Стратегии сопоставления доменов."""

    EXACT = "exact"  # Точное совпадение
    PARTIAL = "partial"  # Частичное совпадение
    HEURISTIC = "heuristic"  # Эвристический анализ


class DomainMatcher:
    """
    Класс для точного сопоставления доменов с типами сервисов.

    Реализует многоуровневую систему распознавания доменов:
    1. Приоритетные домены (точное совпадение)
    2. Полные доменные паттерны
    3. Эвристический анализ по ключевым словам

    Attributes:
        DOMAIN_PATTERNS (Dict[ServiceType, List[str]]): Словарь соответствия
            доменных паттернов типам сервисов
        PRIORITY_DOMAINS (Dict[str, ServiceType]): Словарь приоритетных доменов
            для точного сопоставления
    """

    # Словарь соответствия доменных частей типам сервисов
    DOMAIN_PATTERNS: Dict[ServiceType, List[str]] = {
        ServiceType.YOUTUBE: [
            "youtube.com",
            "www.youtube.com",
            "m.youtube.com",
            "youtu.be",
            "www.youtu.be"
        ],
        ServiceType.INSTAGRAM: [
            "instagram.com",
            "www.instagram.com",
            "instagr.am",
            "www.instagr.am"
        ],
        ServiceType.REDDIT: [
            "reddit.com",
            "www.reddit.com",
            "old.reddit.com",
            "np.reddit.com",
            "amp.reddit.com"
        ],
        ServiceType.RUTUBE: [
            "rutube.ru",
            "www.rutube.ru",
            "rutu.be"
        ],
        ServiceType.TIKTOK: [
            "tiktok.com",
            "www.tiktok.com",
            "vm.tiktok.com",
            "vt.tiktok.com",
            "m.tiktok.com"
        ],
        ServiceType.TWITTER: [
            "twitter.com",
            "www.twitter.com",
            "mobile.twitter.com",
            "m.twitter.com",
            "x.com",
            "www.x.com",
            "mobile.x.com",
            "m.x.com",
        ],
        ServiceType.VK: [
            "vk.com",
            "www.vk.com",
            "m.vk.com",
            "vk.ru",
            "www.vk.ru",
            "vkvideo.ru",
            "www.vkvideo.ru",
        ],
        ServiceType.PINTEREST: [
            "pinterest.com",
            "www.pinterest.com",
            "ru.pinterest.com",
            "pin.it",
            "www.pin.it",
        ],
        ServiceType.VIMEO: [
            "vimeo.com",
            "www.vimeo.com",
            "player.vimeo.com",
        ],
        ServiceType.DAILYMOTION: [
            "dailymotion.com",
            "www.dailymotion.com",
            "dai.ly",
        ],
        ServiceType.FACEBOOK: [
            "facebook.com",
            "www.facebook.com",
            "m.facebook.com",
            "fb.com",
            "fb.watch",
            "www.fb.watch",
        ],
        ServiceType.OKRU: [
            "ok.ru",
            "www.ok.ru",
            "odnoklassniki.ru",
            "www.odnoklassniki.ru",
        ],
        ServiceType.TWITCH: [
            "twitch.tv",
            "www.twitch.tv",
            "clips.twitch.tv",
            "m.twitch.tv",
        ],
        ServiceType.KICK: [
            "kick.com",
            "www.kick.com",
        ],
        ServiceType.RUMBLE: [
            "rumble.com",
            "www.rumble.com",
        ],
        ServiceType.COUB: [
            "coub.com",
            "www.coub.com",
        ],
        ServiceType.SOUNDCLOUD: [
            "soundcloud.com",
            "www.soundcloud.com",
            "on.soundcloud.com",
            "m.soundcloud.com",
        ],
    }

    # Приоритетные домены для точного сопоставления
    PRIORITY_DOMAINS: Dict[str, ServiceType] = {
        "youtu.be": ServiceType.YOUTUBE,
        "rutu.be": ServiceType.RUTUBE,
        "instagr.am": ServiceType.INSTAGRAM,
        "vm.tiktok.com": ServiceType.TIKTOK,
        "vt.tiktok.com": ServiceType.TIKTOK,
        "x.com": ServiceType.TWITTER,
        "twitter.com": ServiceType.TWITTER,
        "vk.com": ServiceType.VK,
        "vkvideo.ru": ServiceType.VK,
        "pin.it": ServiceType.PINTEREST,
        "dai.ly": ServiceType.DAILYMOTION,
        "fb.watch": ServiceType.FACEBOOK,
        "on.soundcloud.com": ServiceType.SOUNDCLOUD,
    }

    # Эвристические правила для частичного сопоставления
    HEURISTIC_RULES: Dict[str, ServiceType] = {
        'youtube': ServiceType.YOUTUBE,
        'youtu': ServiceType.YOUTUBE,
        'instagram': ServiceType.INSTAGRAM,
        'reddit': ServiceType.REDDIT,
        'rutube': ServiceType.RUTUBE,
        'tiktok': ServiceType.TIKTOK,
        "twitter": ServiceType.TWITTER,
        "x.com": ServiceType.TWITTER,
        "vk.com": ServiceType.VK,
        "vk.ru": ServiceType.VK,
        "vkvideo": ServiceType.VK,
        'pinterest': ServiceType.PINTEREST,
        'pin.it': ServiceType.PINTEREST,
        'vimeo': ServiceType.VIMEO,
        'dailymotion': ServiceType.DAILYMOTION,
        'dai.ly': ServiceType.DAILYMOTION,
        'facebook': ServiceType.FACEBOOK,
        'fb.com': ServiceType.FACEBOOK,
        'fb.watch': ServiceType.FACEBOOK,
        'ok.ru': ServiceType.OKRU,
        'odnoklassniki': ServiceType.OKRU,
        'twitch': ServiceType.TWITCH,
        'kick.com': ServiceType.KICK,
        'rumble': ServiceType.RUMBLE,
        'coub': ServiceType.COUB,
        'soundcloud': ServiceType.SOUNDCLOUD,
    }

    @classmethod
    def get_service_type(cls, domain: str) -> ServiceType:
        """
        Определить тип сервиса по домену.

        Использует многоуровневый подход:
        1. Проверка приоритетных доменов (точное совпадение)
        2. Проверка по полным доменным паттернам
        3. Эвристический анализ по ключевым словам

        Args:
            domain: Доменное имя (например, 'youtube.com', 'www.instagram.com')

        Returns:
            ServiceType: Тип сервиса. ServiceType.UNSUPPORTED если домен не поддерживается.

        Example:
            >>> DomainMatcher.get_service_type('youtu.be')
            <ServiceType.YOUTUBE: 'youtube'>

            >>> DomainMatcher.get_service_type('unknown.com')
            <ServiceType.UNSUPPORTED: 'unsupported'>

        Note:
            Регистр домена не имеет значения - преобразование выполняется автоматически.
        """
        domain_lower = domain.lower().strip()

        # Уровень 1: Приоритетные домены (точное совпадение)
        service_type = cls._match_priority_domains(domain_lower)
        if service_type:
            return service_type

        # Уровень 2: Полные доменные паттерны
        service_type = cls._match_domain_patterns(domain_lower)
        if service_type:
            return service_type

        # Уровень 3: Эвристический анализ
        service_type = cls._match_heuristic(domain_lower)
        if service_type:
            return service_type

        return ServiceType.UNSUPPORTED

    @classmethod
    def _match_priority_domains(cls, domain: str) -> Optional[ServiceType]:
        """
        Сопоставление с приоритетными доменами (точное совпадение).

        Args:
            domain: Нормализованное доменное имя в нижнем регистре

        Returns:
            Optional[ServiceType]: Тип сервиса или None если не найдено
        """
        return cls.PRIORITY_DOMAINS.get(domain)

    @classmethod
    def _match_domain_patterns(cls, domain: str) -> Optional[ServiceType]:
        """
        Сопоставление с полными доменными паттернами.

        Проверяет точное совпадение и вхождение в доменные паттерны.

        Args:
            domain: Нормализованное доменное имя в нижнем регистре

        Returns:
            Optional[ServiceType]: Тип сервиса или None если не найдено
        """
        for service_type, patterns in cls.DOMAIN_PATTERNS.items():
            for pattern in patterns:
                if domain == pattern or domain.endswith('.' + pattern):
                    return service_type
        return None

    @classmethod
    def _match_heuristic(cls, domain: str) -> Optional[ServiceType]:
        """
        Эвристический анализ домена по ключевым словам.

        Args:
            domain: Нормализованное доменное имя в нижнем регистре

        Returns:
            Optional[ServiceType]: Тип сервиса или None если не найдено
        """
        for keyword, service_type in cls.HEURISTIC_RULES.items():
            if keyword in domain:
                return service_type
        return None

    @classmethod
    def get_service_type_with_strategy(cls, domain: str) -> tuple[ServiceType, DomainMatchStrategy]:
        """
        Определить тип сервиса и стратегию сопоставления.

        Args:
            domain: Доменное имя

        Returns:
            tuple[ServiceType, DomainMatchStrategy]: Тип сервиса и использованная стратегия

        Example:
            >>> DomainMatcher.get_service_type_with_strategy('youtu.be')
            (<ServiceType.YOUTUBE: 'youtube'>, <DomainMatchStrategy.EXACT: 'exact'>)
        """
        domain_lower = domain.lower().strip()

        # Проверка приоритетных доменов
        if domain_lower in cls.PRIORITY_DOMAINS:
            return cls.PRIORITY_DOMAINS[domain_lower], DomainMatchStrategy.EXACT

        # Проверка доменных паттернов
        for service_type, patterns in cls.DOMAIN_PATTERNS.items():
            for pattern in patterns:
                if domain_lower == pattern or domain_lower.endswith('.' + pattern):
                    return service_type, DomainMatchStrategy.EXACT

        # Эвристический анализ
        for keyword, service_type in cls.HEURISTIC_RULES.items():
            if keyword in domain_lower:
                return service_type, DomainMatchStrategy.HEURISTIC

        return ServiceType.UNSUPPORTED, DomainMatchStrategy.EXACT

    @classmethod
    def get_supported_domains_flat(cls) -> List[str]:
        """
        Получить плоский список всех поддерживаемых доменов.

        Returns:
            List[str]: Отсортированный список уникальных доменных имен

        Example:
            >>> domains = DomainMatcher.get_supported_domains_flat()
            >>> 'youtube.com' in domains
            True
            >>> 'instagram.com' in domains
            True
        """
        domains = []
        for patterns in cls.DOMAIN_PATTERNS.values():
            domains.extend(patterns)
        domains.extend(cls.PRIORITY_DOMAINS.keys())
        return sorted(list(set(domains)))  # Убираем дубликаты и сортируем

    @classmethod
    def is_domain_supported(cls, domain: str) -> bool:
        """
        Проверить, поддерживается ли домен.

        Args:
            domain: Доменное имя для проверки

        Returns:
            bool: True если домен поддерживается, False в противном случае

        Example:
            >>> DomainMatcher.is_domain_supported('youtube.com')
            True
            >>> DomainMatcher.is_domain_supported('unknown.com')
            False
        """
        return cls.get_service_type(domain) != ServiceType.UNSUPPORTED

    @classmethod
    def get_service_domains(cls, service_type: ServiceType) -> List[str]:
        """
        Получить список доменов для конкретного типа сервиса.

        Args:
            service_type: Тип сервиса для которого получить домены

        Returns:
            List[str]: Список доменов для указанного сервиса

        Raises:
            ValueError: Если передан неизвестный тип сервиса

        Example:
            >>> DomainMatcher.get_service_domains(ServiceType.YOUTUBE)
            ['youtube.com', 'www.youtube.com', 'm.youtube.com', 'youtu.be', 'www.youtu.be']
        """
        if service_type not in cls.DOMAIN_PATTERNS:
            raise ValueError(f"Неизвестный тип сервиса: {service_type}")

        return cls.DOMAIN_PATTERNS[service_type].copy()

    @classmethod
    def get_supported_services(cls) -> List[ServiceType]:
        """
        Получить список всех поддерживаемых типов сервисов.

        Returns:
            List[ServiceType]: Список поддерживаемых типов сервисов

        Example:
            >>> services = DomainMatcher.get_supported_services()
            >>> ServiceType.YOUTUBE in services
            True
        """
        return list(cls.DOMAIN_PATTERNS.keys())

    @classmethod
    def add_custom_domain(cls, domain: str, service_type: ServiceType, is_priority: bool = False) -> None:
        """
        Добавить пользовательский домен в маппер.

        Args:
            domain: Доменное имя для добавления
            service_type: Тип сервиса для сопоставления
            is_priority: Если True, добавляется в приоритетные домены

        Example:
            >>> DomainMatcher.add_custom_domain('my.youtube.com', ServiceType.YOUTUBE)
            >>> DomainMatcher.is_domain_supported('my.youtube.com')
            True
        """
        domain_lower = domain.lower().strip()

        if is_priority:
            cls.PRIORITY_DOMAINS[domain_lower] = service_type
        else:
            if service_type not in cls.DOMAIN_PATTERNS:
                cls.DOMAIN_PATTERNS[service_type] = []
            cls.DOMAIN_PATTERNS[service_type].append(domain_lower)