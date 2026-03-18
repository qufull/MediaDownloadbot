from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State


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


def get_tribute_payment_keyboard(url) -> InlineKeyboardMarkup:

    builder = InlineKeyboardBuilder()
    builder.add(
        InlineKeyboardButton(
            text="⭐️ Купить Premium",
            url=str(url)
        )
    )
    return builder.as_markup()

class BroadcastState(StatesGroup):
    waiting_for_message = State()

# Клавиатура выбора аудитории
def get_broadcast_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Отправить ВСЕМ", callback_data="broadcast:all")],
        [InlineKeyboardButton(text="🎯 Отправить НЕ премиум", callback_data="broadcast:free")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="broadcast:cancel")]
    ])