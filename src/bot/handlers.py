import asyncio
import hashlib
from datetime import datetime
from typing import List, Dict
from urllib.parse import urlparse

from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram import Router, F
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, InputMediaPhoto, InputMediaVideo, CallbackQuery, \
    InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import CommandObject, CommandStart, Command, StateFilter  # <-- ДОБАВЛЕН StateFilter
from aiogram.methods import SendMessage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from src.utils.massbots_balance import get_massbots_balance
from src.utils.utils import get_ref_stats_text
from .filters import URLFilter
from .common import ServiceType
from .keyboards import get_admin_keyboard, get_tribute_payment_keyboard, get_broadcast_keyboard
from .patterns import DomainMatcher
from .texts import MessageTemplates

from src.config import bot, settings, user_registry, media_rate_limiter
from src.celery_app.tasks.media_extractor_worker import extract_info

import logging

logger = logging.getLogger(__name__)

ERROR_AUTO_DELETE_SECONDS = 10

router = Router(name=__name__)


async def _auto_delete(bot_instance, chat_id: int, message_id: int, delay: int = ERROR_AUTO_DELETE_SECONDS) -> None:
    """Удаляет сообщение через указанное количество секунд."""
    await asyncio.sleep(delay)
    try:
        await bot_instance.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception as e:
        logger.warning(f"[auto_delete] Не удалось удалить сообщение {message_id}: {e}")


class BroadcastStates(StatesGroup):
    waiting_for_message = State()


@router.message(CommandStart())
async def handle_start(message: Message, command: CommandObject, state: FSMContext) -> None:
    await state.clear()  # <-- СБРОС СОСТОЯНИЯ

    user_id = message.from_user.id
    args = command.args

    # --- 1. ЛОГИКА VPN ПРЕМИУМА ---
    if args and args.startswith("vpn_"):
        parts = args.split("_")
        if len(parts) == 4:
            _, tg_id_str, days_str, signature = parts
            shared_secret = "rAdi8YYvr54ghTjv97TTZxQ1BSwpELkjfgj9Ft07TDC0BJIY4l73L8n0oanRIHzMX7p5aP4NHVlzkQOoabOmduek3c2NMQT10zpAPgINSAI9zf5UaNHrHSZ5Iuxqgqhr"  # Лучше вынести в config/env!
            expected_string = f"{tg_id_str}:{days_str}:{shared_secret}"
            expected_signature = hashlib.sha256(expected_string.encode()).hexdigest()[:16]

            if signature == expected_signature and str(user_id) == tg_id_str:
                try:
                    days_to_add = int(days_str)
                    user_registry.set_premium(user_id, days=days_to_add)
                    await message.answer(
                        f"🎉 <b>Premium активирован!</b>\n\n"
                        f"⭐️ Вам начислено <b>{days_to_add}</b> дн. доступа.",
                        parse_mode="HTML"
                    )
                except ValueError:
                    await message.answer("❌ Ошибка в формате данных.")
                return  # Завершаем, так как это просто выдача према по ссылке
            else:
                await message.answer("⚠️ Ошибка проверки доступа.")
                return

    # --- 2. ЛОГИКА РЕФЕРАЛКИ И РЕГИСТРАЦИИ ---
    username = message.from_user.username
    referrer_id = None

    if args and args.startswith("ref_"):
        try:
            potential_ref = int(args.replace("ref_", ""))
            if potential_ref != user_id:
                referrer_id = potential_ref
        except ValueError:
            pass

    is_new = user_registry.add_user(user_id=user_id, username=username, referrer_id=referrer_id)

    if is_new and referrer_id:
        try:
            await bot.send_message(
                chat_id=referrer_id,
                text="🎁 <b>У вас новый реферал!</b>\nВаш ежедневный лимит увеличен на <b>2</b> ",
                parse_mode="HTML"
            )
        except Exception:
            pass

    welcome_text = MessageTemplates.WELCOME

    media = [
        InputMediaPhoto(
            media="AgACAgIAAxkBAAKeqWlrj7_Ndxa3XuVPw1ketR9bhyVMAAI0Dmsb3a9hSxGlKwkrg0FQAQADAgADeQADNgQ",
            caption=welcome_text,
            parse_mode="HTML",
        ),
        InputMediaPhoto(media="AgACAgIAAxkBAAKeK2lrfydNOln5bbmhz4KfS8ylyiFwAAI1DWsb3a9hS2W9ydJUXSGvAQADAgADeQADNgQ"),
        InputMediaPhoto(media="AgACAgIAAxkBAAKeLWlrf1OMccWuIoSSA37hVDKsNgNOAAJEDWsb3a9hS1z8wwe84IO1AQADAgADeQADNgQ"),
        InputMediaPhoto(media="AgACAgIAAxkBAAKeL2lrf2LxLcJ3jvf6Ltpp8OXxBEogAAJFDWsb3a9hS1isoqXax2Y1AQADAgADeQADNgQ"),
    ]

    await message.answer_media_group(media)


class GiveDaysStates(StatesGroup):
    waiting_for_days = State()


# --- АДМИН ПАНЕЛЬ ---

@router.message(Command("admin"))
async def handle_admin_command(message: Message) -> None:
    """Вход в админ-панель."""
    if message.from_user.id not in settings.telegram.admin_ids:
        return  # Игнорируем, если пишет не админ

    await message.answer(
        "🛠 <b>Панель администратора</b>\nВыберите действие на клавиатуре ниже:",
        reply_markup=get_admin_keyboard(),
        parse_mode="HTML"
    )


@router.message(F.text == "👥 Показать кол-во пользователей")
async def handle_show_users_count(message: Message) -> None:
    """Статистика пользователей."""
    if message.from_user.id not in settings.telegram.admin_ids:
        return

    # Берем точные списки из базы
    all_users = user_registry.get_all_users()
    free_users = user_registry.get_free_users()
    premium_users = user_registry.get_premium_users()

    total_count = len(all_users) if all_users else 0
    free_count = len(free_users) if free_users else 0
    premium_count = len(premium_users) if premium_users else 0

    text = (
        "📊 <b>Статистика пользователей:</b>\n\n"
        f"👥 Всего активных: <b>{total_count}</b>\n"
        f"🆓 Обычные (Free): <b>{free_count}</b>\n"
        f"⭐️ Премиум (Premium): <b>{premium_count}</b>"
    )

    await message.answer(text, parse_mode="HTML")


# --- ВЫДАЧА ДНЕЙ ---

@router.message(F.text == "🎁 Выдача дней")
async def handle_give_days_start(message: Message, state: FSMContext) -> None:
    if message.from_user.id not in settings.telegram.admin_ids:
        return

    await state.set_state(GiveDaysStates.waiting_for_days)
    await message.answer(
        "На сколько дней выдать Premium <b>ВСЕМ активным пользователям</b>?",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="❌ Отмена")]],
            resize_keyboard=True
        ),
        parse_mode="HTML"
    )


@router.message(GiveDaysStates.waiting_for_days)
async def process_give_days_amount(message: Message, state: FSMContext) -> None:
    if message.from_user.id not in settings.telegram.admin_ids:
        return

    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("Отменено", reply_markup=get_admin_keyboard())
        return

    if not message.text.isdigit():
        await message.answer("Количество дней должно быть числом. Попробуйте еще раз:")
        return

    days_to_give = int(message.text)

    # === Массовое обновление БД ===
    user_registry.set_premium_all(days=days_to_give)

    # Получаем количество юзеров для красивого отчета
    users_count = user_registry.get_users_count()

    await state.clear()
    await message.answer(
        f"✅ Успешно! <b>{users_count}</b> пользователям выдано <b>{days_to_give}</b> дней Premium!",
        reply_markup=get_admin_keyboard(),
        parse_mode="HTML"
    )

    await message.answer(
        "💡 <i>Подсказка:</i> Чтобы рассказать пользователям о подарке, сделайте пост через кнопку <b>📤 Рассылка</b>.",
        parse_mode="HTML"
    )


class ManagePremiumStates(StatesGroup):
    waiting_for_username = State()
    waiting_for_days = State()


@router.message(F.text == "👑 Управление Premium")
async def manage_premium_start(message: Message, state: FSMContext) -> None:
    if message.from_user.id not in settings.telegram.admin_ids:
        return

    await state.set_state(ManagePremiumStates.waiting_for_username)
    await message.answer(
        "Введите <b>юзернейм</b> пользователя (с @ или без):",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="❌ Отмена")]],
            resize_keyboard=True
        ),
        parse_mode="HTML"
    )


# 2. Поиск пользователя и выдача кнопок Выдать/Забрать
@router.message(ManagePremiumStates.waiting_for_username)
async def process_premium_username(message: Message, state: FSMContext) -> None:
    if message.from_user.id not in settings.telegram.admin_ids:
        return

    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("Отменено", reply_markup=get_admin_keyboard())
        return

    target_user_id = user_registry.get_user_id_by_username(message.text)

    if not target_user_id:
        await message.answer("❌ Пользователь с таким юзернеймом не найден в базе. Попробуйте еще раз:")
        return

    # Рисуем инлайн-кнопки
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Выдать дни", callback_data=f"prem_give:{target_user_id}")],
        [InlineKeyboardButton(text="➖ Забрать Premium", callback_data=f"prem_take:{target_user_id}")]
    ])

    await state.clear()  # Сбрасываем состояние, дальше работают кнопки
    await message.answer(
        f"✅ Пользователь <b>{message.text}</b> найден (ID: <code>{target_user_id}</code>).\n"
        "Выберите действие:",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


# 3. Обработка кнопки "Забрать"
@router.callback_query(F.data.startswith("prem_take:"))
async def take_premium_callback(call: CallbackQuery) -> None:
    if call.from_user.id not in settings.telegram.admin_ids:
        return

    target_user_id = int(call.data.split(":")[1])

    # Забираем премиум (передаем 0 дней)
    user_registry.set_premium(user_id=target_user_id, days=0)

    await call.message.edit_text(f"✅ Premium успешно отключен у пользователя <code>{target_user_id}</code>!",
                                 parse_mode="HTML")
    await call.message.answer("Возврат в меню", reply_markup=get_admin_keyboard())

    try:
        await bot.send_message(target_user_id, "⚠️ Ваша Premium-подписка была отключена администратором.")
    except Exception:
        pass


# 4. Обработка кнопки "Выдать" (переводит в режим ожидания количества дней)
@router.callback_query(F.data.startswith("prem_give:"))
async def give_premium_callback(call: CallbackQuery, state: FSMContext) -> None:
    if call.from_user.id not in settings.telegram.admin_ids:
        return

    target_user_id = int(call.data.split(":")[1])

    await state.update_data(target_user_id=target_user_id)
    await state.set_state(ManagePremiumStates.waiting_for_days)

    await call.message.edit_text("Введите <b>количество дней</b> Premium для выдачи:", parse_mode="HTML")


# 5. Финальная выдача дней
@router.message(ManagePremiumStates.waiting_for_days)
async def process_premium_days(message: Message, state: FSMContext) -> None:
    if message.from_user.id not in settings.telegram.admin_ids:
        return

    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("Отменено", reply_markup=get_admin_keyboard())
        return

    if not message.text.isdigit():
        await message.answer("Пожалуйста, введите целое число (например, 30):")
        return

    days = int(message.text)
    data = await state.get_data()
    target_user_id = data['target_user_id']

    # Выдаем дни
    user_registry.set_premium(user_id=target_user_id, days=days)
    await state.clear()

    await message.answer(
        f"✅ Пользователю <code>{target_user_id}</code> успешно выдано <b>{days}</b> дней Premium!",
        reply_markup=get_admin_keyboard(),
        parse_mode="HTML"
    )

    try:
        await bot.send_message(target_user_id, f"🎉 Администратор выдал вам <b>{days}</b> дней Premium подписки!")
    except Exception:
        pass


@router.message(Command("premium"))
async def handle_premium_status(message: Message) -> None:
    user_id = message.from_user.id

    # === ПРОВЕРКА НА АДМИНА ===
    if user_id in settings.telegram.admin_ids:
        text = "⭐️ <b>У вас активна Premium-подписка!</b>\nСрок действия: <b>Навсегда</b>"
        await message.answer(text, parse_mode="HTML")
        return  # Завершаем функцию, базу данных дергать не нужно

    # === ЛОГИКА ДЛЯ ОБЫЧНЫХ ПОЛЬЗОВАТЕЛЕЙ ===
    # Получаем статус и дату из базы
    is_premium, expires = user_registry.get_premium_status(user_id)

    if is_premium:
        dt = datetime.fromtimestamp(expires)
        formatted_date = dt.strftime("%d.%m.%Y")
        text = f"⭐️ <b>У вас активна Premium-подписка!</b>\nСрок действия до: <b>{formatted_date}</b>"

        await message.answer(text, parse_mode="HTML")
    else:
        # Если премиума нет
        text = (
            "😔 <b>У вас нет активной Premium-подписки.</b>\n\n"
            "Premium дает возможность скачивать видео без лимитов в максимальном качестве и без рекламы!\n"
            "Хотите оформить?"
        )
        # Отправляем клавиатуру с кнопкой покупки
        await message.answer(
            text,
            parse_mode="HTML",
            reply_markup=get_tribute_payment_keyboard()
        )


@router.message(F.text == "📤 Рассылка")
async def handle_broadcast_button(message: Message, state: FSMContext) -> None:
    if message.from_user.id not in settings.telegram.admin_ids:
        return

    await state.set_state(BroadcastStates.waiting_for_message)
    await message.answer(
        "📤 Отправьте сообщение для рассылки",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="❌ Отмена")]],
            resize_keyboard=True
        )
    )


@router.message(BroadcastStates.waiting_for_message, F.text == "❌ Отмена")
async def handle_broadcast_cancel(message: Message, state: FSMContext) -> None:
    if message.from_user.id not in settings.telegram.admin_ids:
        return

    await state.clear()
    await message.answer("Отменено", reply_markup=get_admin_keyboard())


async def ask_audience_and_save(message: Message, state: FSMContext, messages: List[Message]):
    """Сохраняет сообщение во временный словарь и выдает инлайн-клавиатуру выбора аудитории"""
    # Сохраняем пост по ID админа
    pending_broadcasts[message.from_user.id] = messages

    # Отправляем сообщение с выбором (клавиатура берется из твоего файла keyboards)
    await message.answer(
        "✅ Сообщение загружено! Кому будем отправлять?",
        reply_markup=get_broadcast_keyboard()
    )


media_group_storage: Dict[str, List[Message]] = {}
pending_broadcasts: Dict[int, List[Message]] = {}


@router.message(BroadcastStates.waiting_for_message)
async def handle_broadcast_message(message: Message, state: FSMContext) -> None:
    if message.from_user.id not in settings.telegram.admin_ids:
        return

    # Обработка кнопки "Отмена"
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("Рассылка отменена", reply_markup=get_admin_keyboard())
        # Удаляем само сообщение об отмене, если нужно
        try:
            await message.delete()
        except:
            pass
        return

    # --- ЛОГИКА СБОРА И ОЧИСТКИ ---

    if message.media_group_id:
        # Если это часть альбома
        if message.media_group_id not in media_group_storage:
            media_group_storage[message.media_group_id] = [message]

            # Ждем чуть меньше секунды, чтобы все фото долетели
            await asyncio.sleep(0.8)

            # Извлекаем все собранные сообщения
            messages = media_group_storage.pop(message.media_group_id)
            await ask_audience_and_save(message, state, messages)
        else:
            # Добавляем фото в уже существующую группу
            media_group_storage[message.media_group_id].append(message)
    else:
        # Одиночное сообщение (текст, фото, видео или документ)
        await ask_audience_and_save(message, state, [message])


@router.callback_query(F.data.startswith("broadcast:"))
async def handle_broadcast_audience(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.from_user.id not in settings.telegram.admin_ids:
        return

    await callback.answer()
    action = callback.data.split(":")[1]

    # Достаем сохраненные сообщения
    messages = pending_broadcasts.pop(callback.from_user.id, None)

    if action == "cancel":
        await state.clear()
        await callback.message.edit_text("🚫 Рассылка отменена.")
        await callback.message.answer("Возврат в меню", reply_markup=get_admin_keyboard())

        # Удаляем исходники, чтобы не засорять чат админа
        if messages:
            for msg in messages:
                try:
                    await msg.delete()
                except Exception:
                    pass
        return

    if not messages:
        await callback.message.edit_text("❌ Ошибка: сообщение для рассылки потеряно. Попробуйте еще раз.")
        await callback.message.answer("Возврат в меню", reply_markup=get_admin_keyboard())
        await state.clear()
        return

    if action == "all":
        users = user_registry.get_all_users()
    elif action == "free":
        users = user_registry.get_free_users()
    else:
        users = []

    if not users:
        await callback.message.edit_text("❌ Выбранная база пользователей пуста.")
        await callback.message.answer("Возврат в меню", reply_markup=get_admin_keyboard())
        await state.clear()
        return

    # Меняем текст на инлайн-кнопках на статус работы
    await callback.message.edit_text(f"🚀 <b>Рассылка запущена для {len(users)} пользователей...</b>", parse_mode="HTML")

    # ВОЗВРАЩАЕМ КНОПКУ СРАЗУ, чтобы она не висела как "Отмена", пока идет долгая рассылка
    await callback.message.answer("Рассылка работает в фоне. Вы можете продолжать пользоваться ботом.",
                                  reply_markup=get_admin_keyboard())

    asyncio.create_task(start_broadcast_and_cleanup(callback.message, state, messages, users))


async def start_broadcast_and_cleanup(message: Message, state: FSMContext, messages: List[Message], users: List[int]):
    """Запуск рассылки и последующее удаление исходных сообщений"""
    await state.clear()

    is_media_group = len(messages) > 1
    media_list = []

    if is_media_group:
        for msg in messages:
            if msg.photo:
                media_list.append(InputMediaPhoto(media=msg.photo[-1].file_id, caption=msg.caption,
                                                  caption_entities=msg.caption_entities))
            elif msg.video:
                media_list.append(InputMediaVideo(media=msg.video.file_id, caption=msg.caption,
                                                  caption_entities=msg.caption_entities))

    sent_count = 0
    blocked_count = 0

    # ОСНОВНОЙ ЦИКЛ РАССЫЛКИ
    for user_id in users:
        try:
            if is_media_group:
                await bot.send_media_group(chat_id=user_id, media=media_list)
            else:
                await bot.copy_message(
                    chat_id=user_id,
                    from_chat_id=messages[0].chat.id,
                    message_id=messages[0].message_id
                )
            sent_count += 1
        except Exception:
            blocked_count += 1
            user_registry.deactivate_user(user_id)

        # Флуд-контроль
        await asyncio.sleep(0.05 if not is_media_group else 0.1)

    # --- ОЧИСТКА ХВОСТОВ ---

    # 1. Удаляем сообщения, которые прислал админ (исходники рассылки)
    for msg in messages:
        try:
            await msg.delete()
        except Exception as e:
            logger.warning(f"Не удалось удалить исходное сообщение: {e}")

    # 2. Отправляем финальный отчет
    await message.answer(
        f"✅ <b>Рассылка завершена!</b>\n\n"
        f"📤 Доставлено: <b>{sent_count}</b>\n",
        reply_markup=get_admin_keyboard(),
        parse_mode="HTML"
    )


@router.message(Command("products"))
async def handle_products(message: Message) -> SendMessage:
    """Обработчик команды /products."""
    products_text = MessageTemplates.PRODUCTS
    await message.answer(text=products_text)


@router.message(Command("help"))
async def handle_help(message: Message) -> SendMessage:
    """Показать расширенную справку."""
    supported_services = []
    for service_type, domains in DomainMatcher.DOMAIN_PATTERNS.items():
        domain_examples = ", ".join(domains[:3])
        supported_services.append(
            f"• <b>{service_type.value.upper()}</b> - {domain_examples}..."
        )

    tail_supported_services = chr(10).join(supported_services)
    help_text = MessageTemplates.HELP.format(tail_supported_services=tail_supported_services)
    await message.answer(text=help_text)


@router.message(Command("donate"))
async def handle_donate(message: Message) -> SendMessage:
    """Обработчик команды /donate — поддержка проекта."""

    donate_text = (
        "Лучшая поддержка от вас — это небольшой регулярный платёж "
        "за сервис, которым вы и так уже пользуетесь ежедневно.\n\n"
        "🛡 <b>Сервис SkyNet VPN.</b>\n"
        "Быстрый. Надёжный. Безопасный."
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛒 Купить подписку", url="https://t.me/vpnskynetai_bot?start=refeZYFehrQ")],
    ])

    await message.answer(text=donate_text, reply_markup=keyboard)


@router.message(URLFilter(check_support=False))
async def handle_url_message(message: Message, state: FSMContext) -> SendMessage:
    """Обработчик сообщений с URL."""
    await state.clear()  # <-- СБРОС СОСТОЯНИЯ

    url = message.text or message.caption
    if not url:
        return

    try:
        parsed = urlparse(url.strip())
        domain = parsed.netloc.lower()
        service_type = DomainMatcher.get_service_type(domain)

        if service_type == ServiceType.UNSUPPORTED:
            await handle_unsupported_domain(domain=domain, message=message)
            return

        # === Проверка дневных лимитов ===
        if message.from_user.id not in settings.telegram.admin_ids:
            if not media_rate_limiter.can_download(message.chat.id, service=service_type.value):

                # Если это обычный пользователь (не премиум)
                if not user_registry.is_user_premium(message.from_user.id):
                    limit_text = await get_ref_stats_text(message.from_user.id)
                    promo_text = (
                        f"{limit_text}\n\n"
                        "⭐️ <b>Хотите качать без ограничений?</b>\n"
                        "Оформите Premium-подписку и забудьте о лимитах и сжатом качестве!"
                    )
                    await message.answer(
                        text=promo_text,
                        reply_markup=get_tribute_payment_keyboard(),
                        parse_mode="HTML"
                    )
                # Если это Premium (исчерпал лимит 30 видео на YouTube)
                else:
                    text = (
                        "⚠️ <b>Дневной лимит YouTube исчерпан.</b>\n"
                        "Для безопасности бота даже на Premium действует лимит: 30 YouTube-видео в день. "
                        "Остальные сервисы (TikTok, Reels и др.) остаются безлимитными. Возвращайтесь завтра!"
                    )
                    await message.answer(text=text, parse_mode="HTML")
                return

        # Если лимиты не превышены (или это админ) — отправляем задачу в парсер
        extract_info.delay(
            url=url,
            chat_id=message.chat.id,
            service=service_type.value,
            message_id=message.message_id,
        )

    except Exception as e:
        error_text = MessageTemplates.ERROR
        error_msg = await message.answer(text=error_text)
        asyncio.create_task(_auto_delete(message.bot, message.chat.id, error_msg.message_id))


async def handle_unsupported_domain(domain: str, message: Message) -> SendMessage:
    """Обработчик неподдерживаемых доменов."""
    supported_services = ", ".join(
        service_type.value for service_type in DomainMatcher.DOMAIN_PATTERNS.keys()
    )

    text = MessageTemplates.UNSUPPORTED_DOMAIN.format(
        domain=domain,
        supported_services=supported_services
    )
    error_msg = await message.answer(text=text)
    asyncio.create_task(_auto_delete(message.bot, message.chat.id, error_msg.message_id))


@router.message(Command("support"))
async def support_command(message: Message):
    builder = InlineKeyboardBuilder()

    # Вставь сюда юзернейм твоего аккаунта поддержки (без @)
    support_username = "skynetaivpn_support"

    # Создаем кнопку-ссылку
    builder.button(
        text="👨‍💻 Написать специалисту",
        url=f"https://t.me/{support_username}"
    )

    await message.answer(
        "📝 **Служба поддержки**\n\n"
        "Если у вас возникли вопросы, проблемы с оплатой или вы нашли баг, "
        "пожалуйста, напишите нам. Мы ответим в кратчайшие сроки!",
        reply_markup=builder.as_markup(),
        parse_mode="Markdown"
    )


@router.message(Command("balance"))
async def massbots_balance_cmd(message: Message):
    try:
        balance = get_massbots_balance()

        if balance is None:
            await message.answer("⚠️ Не удалось получить баланс (нет поля balance в ответе API)")
            return

        text = (
            "📊 <b>MassBots баланс</b>\n\n"
            f"Баланс: <b>{balance}</b>\n"
        )

        await message.answer(text, parse_mode="HTML")

    except Exception as e:
        await message.answer(f"❌ Ошибка получения баланса:\n<code>{e}</code>", parse_mode="HTML")


@router.message(Command("ref"))
async def handle_ref_command(message: Message) -> None:
    """Вся информация о рефералах здесь."""
    user_id = message.from_user.id

    # Считаем данные
    max_allowed = media_rate_limiter.get_max_allowed(user_id)
    remaining = media_rate_limiter.get_remaining(user_id)
    bonus = user_registry.get_bonus(user_id)

    # Генерируем ссылку
    bot_info = await bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start=ref_{user_id}"

    text = (
        "🎁 <b>Реферальная программа</b>\n\n"
        "Приглашайте друзей и увеличивайте свой ежедневный лимит!\n"
        "За каждого приглашенного вы получаете <b>+2</b> к лимиту навсегда.\n\n"
        f"📊 <b>Твоя статистика:</b>\n"
        f"└ Базовый лимит: {media_rate_limiter.DAILY_LIMIT}\n"
        f"└ Бонусы за друзей: +{bonus}\n"
        f"└ Итого доступно: <b>{max_allowed}</b> в сутки\n"
        f"└ Осталось сегодня: <b>{remaining}</b>\n\n"
        f"🔗 <b>Твоя ссылка для приглашения:</b>\n"
        f"<code>{ref_link}</code>"
    )

    await message.answer(text, parse_mode="HTML")


@router.message(StateFilter(None))  # <-- ДОБАВЛЕН StateFilter(None)
async def handle_unknown_message(message: Message) -> None:
    """Обработчик неизвестных сообщений."""
    unknown_text = MessageTemplates.UNKNOWN
    error_msg = await message.answer(text=unknown_text)
    asyncio.create_task(_auto_delete(message.bot, message.chat.id, error_msg.message_id))