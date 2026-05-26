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
        keyboard=[
            [KeyboardButton(text="📤 Рассылка"), KeyboardButton(text="🎁 Выдача дней")],
            [KeyboardButton(text="👑 Управление Premium")],
            [KeyboardButton(text="👥 Показать кол-во пользователей")]
        ],
        resize_keyboard=True
    )


def get_tribute_payment_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="⭐️ Купить Premium",
            url="https://t.me/tribute/app?startapp=psMq"
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="🛡 Подключить SkyNet VPN + Premium",
            url="https://t.me/vpnskynetai_bot?start=refeZYFehrQ"
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


def get_timecode_choice_keyboard(chosen_id: str = "") -> InlineKeyboardMarkup:
    """
    Клавиатура выбора: скачать видео целиком или вырезать фрагмент.
    """
    builder = InlineKeyboardBuilder()

    builder.add(
        InlineKeyboardButton(
            text="🎬 Полностью",
            callback_data="full_dl"
        ),
        InlineKeyboardButton(
            text="✂️ Фрагмент",
            callback_data="frag_dl"
        )
    )

    # Размещаем кнопки в один ряд (две кнопки рядом)
    builder.adjust(2)

    return builder.as_markup()


def get_timecode_cancel_keyboard() -> InlineKeyboardMarkup:
    """
    Клавиатура для отмены ввода таймкодов.
    """
    builder = InlineKeyboardBuilder()

    builder.add(
        InlineKeyboardButton(
            text="❌ Отмена",
            callback_data="tc_cancel"
        )
    )

    return builder.as_markup()