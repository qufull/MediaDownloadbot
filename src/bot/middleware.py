from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.enums import ChatMemberStatus
from aiogram.types import TelegramObject, Message, CallbackQuery

from .texts import MessageTemplates
from .keyboards import get_subscription_keyboard

from src.config import settings, user_registry


class UserRegistrationMiddleware(BaseMiddleware):
    """Middleware для регистрации пользователей."""

    async def __call__(
            self,
            handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
            event: TelegramObject,
            data: Dict[str, Any],
    ) -> Any:
        if hasattr(event, 'from_user') and event.from_user:
            user_registry.add_user(event.from_user.id)
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
        user_id: int = event.from_user.id

        if user_id in self.admin_id_list:
            return await handler(event, data)

        missing_subscriptions = await self._check_subscriptions(event.bot, user_id)

        if missing_subscriptions:
            return await self._send_subscription_request(event, missing_subscriptions)

        return await handler(event, data)

    async def _check_subscriptions(self, bot, user_id: int) -> list:
        missing_subscriptions = []

        for channel_username in self.bot_subscriptions_channels:
            clean_username = channel_username.lstrip('@')

            try:
                chat = await bot.get_chat(f"@{clean_username}")
                chat_id = chat.id

                member = await bot.get_chat_member(
                    chat_id=chat_id,
                    user_id=user_id,
                )
                if member.status not in (
                        ChatMemberStatus.MEMBER,
                        ChatMemberStatus.ADMINISTRATOR,
                        ChatMemberStatus.CREATOR,
                ):
                    missing_subscriptions.append({
                        'chat_id': chat_id,
                        'username': f"@{clean_username}",
                        'title': chat.title if hasattr(chat, 'title') else f"@{clean_username}"
                    })
            except Exception:
                missing_subscriptions.append({
                    'chat_id': None,
                    'username': f"@{clean_username}",
                    'title': f"@{clean_username}"
                })

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