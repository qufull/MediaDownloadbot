import logging
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.enums import ChatMemberStatus
from aiogram.types import TelegramObject, Message, CallbackQuery

from .texts import MessageTemplates
from .keyboards import get_subscription_keyboard

from src.config import settings, user_registry

logger = logging.getLogger(__name__)


class UserRegistrationMiddleware(BaseMiddleware):
    """
    Middleware для регистрации пользователей.
    Пропускает регистрацию для команды /start, чтобы хендлер мог
    самостоятельно обработать реферальный ID.
    """

    async def __call__(
            self,
            handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
            event: TelegramObject,
            data: Dict[str, Any],
    ) -> Any:
        # Проверяем, есть ли в событии данные о пользователе
        user = getattr(event, "from_user", None)

        if user and not user.is_bot:
            user_id = user.id

            # Определяем, является ли текущее сообщение командой /start
            is_start_command = False
            if isinstance(event, Message) and event.text:
                is_start_command = event.text.strip().startswith("/start")

            # Логика:
            # Если это НЕ /start, регистрируем пользователя (обычная активность)
            # Если это /start, ничего не делаем — хендлер handle_start сам вызовет
            # user_registry.add_user с нужным referrer_id.
            if not is_start_command:
                user_registry.add_user(user_id)

        return await handler(event, data)


class IsAdminMiddleware(BaseMiddleware):
    """Middleware для проверки подписок на каналы."""

    def __init__(self) -> None:
        super().__init__()
        self.admin_id_list: frozenset[int] = settings.telegram.admin_ids
        self.bot_subscriptions_channels: frozenset[str] = settings.telegram.subscription_channels

    async def __call__(
            self,
            handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
            event: TelegramObject,
            data: Dict[str, Any],
    ) -> Any:
        # Безопасное получение пользователя через data
        user = data.get("event_from_user")
        if not user:
            return await handler(event, data)

        user_id: int = user.id

        # Проверка на админа
        if user_id in self.admin_id_list:
            return await handler(event, data)

        # Проверка подписок
        missing_subscriptions = await self._check_subscriptions(event.bot, user_id)

        if missing_subscriptions:
            return await self._send_subscription_request(event, missing_subscriptions)

        return await handler(event, data)

    async def _check_subscriptions(self, bot, user_id: int) -> list:
        missing_subscriptions = []

        for channel_username in self.bot_subscriptions_channels:
            clean_username = channel_username.lstrip('@')

            try:
                # В новых версиях лучше сразу обращаться к @username,
                # но get_chat_member требует chat_id (инт или линк)
                member = await bot.get_chat_member(
                    chat_id=f"@{clean_username}",
                    user_id=user_id,
                )

                if member.status not in (
                        ChatMemberStatus.MEMBER,
                        ChatMemberStatus.ADMINISTRATOR,
                        ChatMemberStatus.CREATOR,
                ):
                    # Если не подписан, получаем инфо о чате для заголовка
                    chat = await bot.get_chat(f"@{clean_username}")
                    missing_subscriptions.append({
                        'chat_id': chat.id,
                        'username': f"@{clean_username}",
                        'title': chat.title
                    })
            except Exception as e:
                # Бот не является админом канала — пропускаем проверку
                # (нельзя проверить членство без прав администратора)
                logger.warning(f"Пропуск проверки подписки в {clean_username}: {e}")

        return missing_subscriptions

    async def _send_subscription_request(self, event: TelegramObject, missing_subscriptions: list) -> Any:
        if isinstance(event, Message):
            answer_method = event.answer
        elif isinstance(event, CallbackQuery):
            answer_method = event.message.answer
        else:
            return

        reply_markup = get_subscription_keyboard(missing_subscriptions=missing_subscriptions)

        if len(missing_subscriptions) == 1:
            channel_name = missing_subscriptions[0]['title']
            text = MessageTemplates.SUBSCRIPTION_REQUEST_ONE.format(channel_name=channel_name)
        else:
            channels_list = "\n".join([f"• {channel['title']}" for channel in missing_subscriptions])
            text = MessageTemplates.SUBSCRIPTION_REQUEST_MANY.format(channel_list=channels_list)

        return await answer_method(text=text, reply_markup=reply_markup)