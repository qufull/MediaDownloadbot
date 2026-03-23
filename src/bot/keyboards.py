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


def get_timecode_cancel_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.add(InlineKeyboardButton(text="❌ Отмена", callback_data="tc_cancel"))
    return builder.as_markup()


def get_timecode_choice_keyboard(video_key: str) -> InlineKeyboardMarkup:
    """
    Показывается после выбора качества.
    video_key — идентификатор из сессии (format_id видео), например '1080p'.
    """
    builder = InlineKeyboardBuilder()
    builder.add(InlineKeyboardButton(text="🎬 Полностью", callback_data=f"full_dl:{video_key}"))
    builder.add(InlineKeyboardButton(text="✂️ Фрагмент", callback_data=f"frag_dl:{video_key}"))
    builder.add(InlineKeyboardButton(text="❌ Отмена", callback_data="tc_cancel"))
    builder.adjust(2, 1)
    return builder.as_markup()


def get_delivery_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура выбора куда отправить готовое видео (< 1 ГБ)."""
    builder = InlineKeyboardBuilder()
    builder.add(InlineKeyboardButton(text="📱 В Telegram", callback_data="deliver_tg"))
    builder.add(InlineKeyboardButton(text="🔗 По ссылке", callback_data="deliver_drive"))
    builder.adjust(2)
    return builder.as_markup()


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