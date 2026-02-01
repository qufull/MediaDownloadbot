from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton


def get_subscription_keyboard(missing_subscriptions: list) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    
    for channel in missing_subscriptions:
        channel_link = f"https://t.me/{channel['username'].lstrip('@')}"
        
        builder.add(
            InlineKeyboardButton(
                text=f"📢 Подписаться на {channel['title']}",
                url=channel_link
            )
        )
        
    builder.add(
        InlineKeyboardButton(
            text="✅ Проверить подписку",
            callback_data="check_subscription"
        )
    )
    
    builder.adjust(1)
    
    return builder.as_markup()


def get_retry_subscription_keyboard(missing_subscriptions: list) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    
    for channel in missing_subscriptions:
        channel_link = f"https://t.me/{channel['username'].lstrip('@')}"
        
        builder.add(
            InlineKeyboardButton(
                text=f"📢 Подписаться на {channel['title']}",
                url=channel_link
            )
        )
        
    builder.add(
        InlineKeyboardButton(
            text="🔄 Проверить снова",
            callback_data="check_subscription"
        )
    )
    
    builder.adjust(1)
    
    return builder.as_markup()


def get_admin_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📤 Рассылка")]],
        resize_keyboard=True
    )
