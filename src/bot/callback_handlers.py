import asyncio
import re
import random
from typing import Dict, List

from aiogram import Router, F
from aiogram.enums import ChatAction, ChatMemberStatus
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.methods import AnswerCallbackQuery
from aiogram.types import CallbackQuery, InputMediaPhoto, FSInputFile, Message
from aiogram.utils.chat_action import ChatActionSender

from src.utils.ads import send_vpn_ad
from src.utils.utils import get_ref_stats_text
from .texts import MessageTemplates
from .common import exetract_image_name
from .keyboards import (
    get_retry_subscription_keyboard,
    get_tribute_payment_keyboard,
    get_timecode_choice_keyboard,
    get_timecode_cancel_keyboard
)

from src.config import bot, settings, user_registry
from src.core import ImageDictAnnotation
from src.config import user_session_storage, user_activity_queue, media_rate_limiter

from src.utils.telegram_anim import send_error

from src.celery_app.tasks.audio_download_worker import download_audio
from src.celery_app.tasks.video_download_worker import (
    download_reddit_video,
    download_rutube_video,
    download_tiktok_video,
    download_youtube_video,
    download_twitter_video,
    download_instagram_video,
    download_vk_video,
    download_pinterest_video,
)

router = Router(name=__name__)


# ---------------------------------------------------------------------------
# FSM: Состояния для ввода таймкодов
# ---------------------------------------------------------------------------
class TimecodeStates(StatesGroup):
    waiting_for_timecodes = State()


# ---------------------------------------------------------------------------
# Карта задач Celery
# ---------------------------------------------------------------------------
_TASK_MAP = {
    "twitter": download_twitter_video,
    "youtube": download_youtube_video,
    "reddit": download_reddit_video,
    "rutube": download_rutube_video,
    "tiktok": download_tiktok_video,
    "instagram": download_instagram_video,
    "vk": download_vk_video,
    "pinterest": download_pinterest_video,
}


def _get_task_func_for_service(service: str):
    return _TASK_MAP.get(service)


def _safe_media_id(item: dict) -> str | None:
    return (item.get("name") or item.get("id") or "").strip() or None


def _parse_timecode_range(text: str):
    """
    Парсит диапазон таймкодов из строки:
      '1:30-5:00'  → (90, 300)
      '01:30-05:00' → (90, 300)
    """

    def _to_sec(s: str) -> int:
        parts = [int(x) for x in s.split(":")]
        if len(parts) == 1: return parts[0]
        if len(parts) == 2: return parts[0] * 60 + parts[1]
        return parts[0] * 3600 + parts[1] * 60 + parts[2]

    text = text.strip().replace(" ", "")
    m = re.match(r'^(\d{1,2}(?::\d{2}){0,2})-(\d{1,2}(?::\d{2}){0,2})$', text)
    if not m: return None

    try:
        start = _to_sec(m.group(1))
        end = _to_sec(m.group(2))
    except (ValueError, IndexError):
        return None

    if start >= end or end <= 0: return None
    return start, end


# ---------------------------------------------------------------------------
# Вспомогательные функции отправки на загрузку
# ---------------------------------------------------------------------------
async def process_queue_and_download(chat_id, message_id, url, width, height, chosen_id, merge_audio, delay, task_func):
    """Фоновая задача: держит юзера в очереди, показывает таймер, затем отдает в скачивание"""
    try:
        timer_msg = await bot.send_video(
            chat_id=chat_id,
            video=FSInputFile("src/assets/timer.mp4"),
            caption=(
                "⏳ <b>Ваше видео поставлено в очередь!</b>\n"
                f"Ожидаемое время до начала выгрузки: ~3 мин.\n\n"
                "⭐️ <i>Premium-пользователям доступна выгрузка моментально и без очереди!</i>"
            ),
            parse_mode="HTML",
            reply_to_message_id=message_id,
            reply_markup=get_tribute_payment_keyboard()
        )
    except Exception:
        timer_msg = None

    await asyncio.sleep(delay)

    if timer_msg:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=timer_msg.message_id)
        except Exception:
            pass

    task_func.delay(
        url=url, width=width, height=height, chat_id=chat_id,
        message_id=message_id, video_id=chosen_id, merge_audio=merge_audio
    )


async def _dispatch_download(chat_id, message_id, url, width, height, chosen_id, merge_audio, is_premium, is_admin,
                             service):
    task = _get_task_func_for_service(service)
    if not task:
        await send_error(chat_id=chat_id, text=f"Сервис {service} пока не поддерживается для скачивания.")
        user_activity_queue.delete_download(chat_id=chat_id)
        return

    if is_premium or is_admin:
        task.delay(
            url=url, width=width, height=height, chat_id=chat_id,
            message_id=message_id, video_id=chosen_id, merge_audio=merge_audio
        )
    else:
        delay = random.randint(60, 180)
        asyncio.create_task(
            process_queue_and_download(chat_id, message_id, url, width, height, chosen_id, merge_audio, delay, task)
        )


# ---------------------------------------------------------------------------
# Основные хэндлеры
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Основные хэндлеры
# ---------------------------------------------------------------------------
@router.callback_query(F.data.startswith("video"))
async def handle_video(callback: CallbackQuery, state: FSMContext) -> AnswerCallbackQuery:
    chat_id = callback.message.chat.id
    await callback.answer()

    session = user_session_storage.get_session(chat_id=chat_id)
    if session is None:
        await send_error(chat_id=chat_id, text=MessageTemplates.CALLBACK_SESSION_EXPIRED)
        return

    service = session["service"]
    user_id = callback.from_user.id
    is_admin = user_id in settings.telegram.admin_ids
    is_premium = user_registry.is_user_premium(user_id)

    # Проверка лимитов
    if not is_admin:
        if not media_rate_limiter.can_download(chat_id, service=service):
            if not is_premium:
                limit_text = await get_ref_stats_text(user_id)
                promo_text = f"{limit_text}\n\n⭐️ <b>Хотите качать без ограничений?</b>\nОформите Premium-подписку!"
                await bot.send_message(
                    chat_id=chat_id, text=promo_text,
                    reply_markup=get_tribute_payment_keyboard(), parse_mode="HTML"
                )
            else:
                text = "⚠️ <b>Дневной лимит YouTube исчерпан.</b>\nОстальные сервисы остаются безлимитными. Возвращайтесь завтра!"
                await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
            return

    if user_activity_queue.get_download(chat_id=chat_id):
        await send_error(chat_id=chat_id, text=MessageTemplates.CALLBACK_PROCESSING_MESSAGE)
        return

    try:
        _, video_id = callback.data.split(":", 1)
        video = next((v for v in session["media_data"]["videos"] if str(_safe_media_id(v)) == str(video_id)), None)
        if not video:
            await send_error(chat_id=chat_id, text="Не нашла выбранный формат видео. Отправь ссылку заново.")
            return

        url = session["url"]
        width, height = video.get("width"), video.get("height")
        w, h = int(width) if width else 0, int(height) if height else 0

        quality = min(w, h) if (w > 0 and h > 0) else (h if h > 0 else w)
        if h > 0:
            quality = {1920: 1080, 1280: 720, 854: 480, 640: 360, 2560: 1440, 3840: 2160}.get(h, h)

        # Проверка на Premium качество
        if service in ["youtube", "rutube", "vk"] and quality >= 720:
            if not is_premium and not is_admin:
                promo_text = (
                    "⭐️ <b>Высокое качество (720p и выше) доступно только по Premium-подписке!</b>\n\n"
                    "🔥 <b>PREMIUM:</b> 1080p/720p, без рекламы, приоритет в загрузке\n"
                    "🆓 <b>FREE:</b> Качество ≤ 640p, реклама, очередь\n\n"
                    "Подключи <a href=\"https://t.me/vpnskynetai_bot?start=refeZYFehrQ\">SkyNet VPN</a> "
                    "и используй Premium бесплатно.\n"
                    "<i>(После покупки VPN необходимо написать в поддержку)</i>\n\n"
                    "<blockquote>"
                    "🏳️ Обход белых списков\n"
                    "🌍 9 разных стран / до 8 устройств\n"
                    "📹 Youtube в режиме \"окно в окне\"\n"
                    "🎁 Proxy Telegram + Canva Pro в подарок\n"
                    "⚡️ Высокая скорость без урезаний"
                    "</blockquote>"
                )
                await callback.message.answer(
                    promo_text, reply_markup=get_tribute_payment_keyboard(), parse_mode="HTML"
                )
                return

        chosen_id = _safe_media_id(video)
        if not chosen_id:
            await send_error(chat_id=chat_id, text="У видео нет идентификатора формата (name/id).")
            return

        if _get_task_func_for_service(service) is None:
            await send_error(chat_id=chat_id, text=f"Сервис {service} пока не поддерживается.")
            return

        user_activity_queue.create_download(url=url, chat_id=chat_id, service=service)
        merge_audio = not video.get("has_audio", False)

        # === ИЗМЕНЕНИЯ ЗДЕСЬ ===
        if service == "youtube":
            # Для YouTube сразу запускаем скачивание целиком (игнорируем выбор фрагмента)
            await _dispatch_download(
                chat_id=chat_id, message_id=callback.message.message_id,
                url=url, width=width, height=height,
                chosen_id=chosen_id, merge_audio=merge_audio,
                is_premium=is_premium, is_admin=is_admin,
                service=service
            )
        else:
            # Предлагаем пользователю выбор для остальных платформ
            await state.update_data(
                url=url, width=width, height=height, service=service,
                video_key=chosen_id, merge_audio=merge_audio,
                message_id=callback.message.message_id,
                is_premium=is_premium, is_admin=is_admin,
            )

            await bot.send_message(
                chat_id=chat_id,
                text="🎬 <b>Скачать целиком или выбрать фрагмент?</b>\n\n• <b>Полностью</b> — скачать всё видео\n• <b>Фрагмент</b> — выбрать отрезок",
                reply_markup=get_timecode_choice_keyboard(chosen_id),
                parse_mode="HTML",
            )

    except Exception:
        await send_error(chat_id=chat_id, text="Ошибка при обработке видео. Попробуй ещё раз.")
        user_activity_queue.delete_download(chat_id=chat_id)
        raise

# ---------------------------------------------------------------------------
# Выбор "Полностью"
# ---------------------------------------------------------------------------
@router.callback_query(F.data.startswith("full_dl"))
async def handle_full_download(callback: CallbackQuery, state: FSMContext) -> None:
    chat_id = callback.message.chat.id
    await callback.answer()

    data = await state.get_data()
    await state.clear()

    if not data:
        await send_error(chat_id=chat_id, text="Сессия истекла. Отправь ссылку заново.")
        return

    try:
        await callback.message.delete()
    except Exception:
        pass

    await _dispatch_download(
        chat_id=chat_id, message_id=data.get("message_id", callback.message.message_id),
        url=data["url"], width=data.get("width"), height=data.get("height"),
        chosen_id=data["video_key"], merge_audio=data.get("merge_audio", False),
        is_premium=data.get("is_premium", False), is_admin=data.get("is_admin", False),
        service=data["service"],
    )


# ---------------------------------------------------------------------------
# Выбор "Фрагмент"
# ---------------------------------------------------------------------------
@router.callback_query(F.data.startswith("frag_dl"))
async def handle_fragment_choice(callback: CallbackQuery, state: FSMContext) -> None:
    chat_id = callback.message.chat.id
    await callback.answer()

    data = await state.get_data()
    if not data:
        await send_error(chat_id=chat_id, text="Сессия истекла. Отправь ссылку заново.")
        return

    await state.set_state(TimecodeStates.waiting_for_timecodes)

    tc_text = (
        "⏱ <b>Введите таймкоды фрагмента</b>\n\n"
        "Формат: <code>начало-конец</code>\n\n"
        "Примеры:\n"
        "• <code>1:30-5:00</code>\n"
        "• <code>00:01:30-00:05:00</code>\n"
        "• <code>30-90</code> (в секундах)"
    )

    try:
        await callback.message.edit_text(tc_text, parse_mode="HTML", reply_markup=get_timecode_cancel_keyboard())
    except Exception:
        await bot.send_message(chat_id=chat_id, text=tc_text, parse_mode="HTML",
                               reply_markup=get_timecode_cancel_keyboard())


# ---------------------------------------------------------------------------
# Отмена ввода таймкода
# ---------------------------------------------------------------------------
@router.callback_query(F.data == "tc_cancel")
async def handle_timecode_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    chat_id = callback.message.chat.id
    await callback.answer("Отменено")
    await state.clear()
    user_activity_queue.delete_download(chat_id=chat_id)
    try:
        await callback.message.edit_text("❌ Скачивание отменено.")
    except Exception:
        await bot.send_message(chat_id=chat_id, text="❌ Скачивание отменено.")


# ---------------------------------------------------------------------------
# Обработка введенного сообщения с таймкодами
# ---------------------------------------------------------------------------
@router.message(TimecodeStates.waiting_for_timecodes)
async def handle_timecodes_input(message: Message, state: FSMContext) -> None:
    chat_id = message.chat.id
    data = await state.get_data()

    parsed = _parse_timecode_range(message.text or "")
    if not parsed:
        # Если юзер ошибся, мы НЕ делаем await state.clear(),
        # чтобы он мог просто прислать исправленный вариант следующим сообщением.
        await message.answer(
            "❌ Неверный формат таймкодов.\n\n"
            "Попробуйте ещё раз, просто отправьте исправленные цифры (например, <b>3-5</b>).",
            parse_mode="HTML"
        )
        return

    # Если формат верный — только теперь очищаем состояние ожидания
    await state.clear()

    start_sec, end_sec = parsed
    video_key = data.get("video_key", "")
    video_id_with_tc = f"{video_key}|{start_sec}-{end_sec}"

    def _fmt_tc(sec: int) -> str:
        h, m, s = sec // 3600, (sec % 3600) // 60, sec % 60
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

    await message.answer(f"✂️ Скачиваю фрагмент <b>{_fmt_tc(start_sec)}–{_fmt_tc(end_sec)}</b>...", parse_mode="HTML")

    await _dispatch_download(
        chat_id=chat_id, message_id=data.get("message_id", message.message_id),
        url=data.get("url", ""), width=data.get("width"), height=data.get("height"),
        chosen_id=video_id_with_tc, merge_audio=data.get("merge_audio", False),
        is_premium=data.get("is_premium", False), is_admin=data.get("is_admin", False),
        service=data.get("service", "")
    )


# ---------------------------------------------------------------------------
# Обработка Изображений
# ---------------------------------------------------------------------------
@router.callback_query(F.data.startswith("image"))
async def handle_image(callback: CallbackQuery) -> AnswerCallbackQuery:
    chat_id = callback.message.chat.id
    await callback.answer()

    session = user_session_storage.get_session(chat_id=chat_id)
    if session is None:
        await send_error(chat_id=chat_id, text=MessageTemplates.CALLBACK_SESSION_EXPIRED)
        return

    try:
        images_by_name: Dict[str, List[ImageDictAnnotation]] = {}
        for image in session["media_data"]["images"]:
            filename = exetract_image_name(image_url=image["url"])
            if filename not in images_by_name:
                images_by_name[filename] = []
            images_by_name[filename].append(image)

        best_images = [images_by_name[name][-1] for name in images_by_name.keys()]

        if not best_images:
            await send_error(chat_id=chat_id, text="Изображений не найдено.")
            return

        media = []
        for i, image in enumerate(best_images):
            caption = ""
            if i == 0:
                caption = MessageTemplates.CALLBACK_IMAGE_CAPTION.format(
                    url=session["url"],
                    quantity=len(best_images),
                    service=session["service"],
                    botname=settings.telegram.name,
                    author_name=session["media_data"].get("author_name", "Unknown"),
                )
            media.append(InputMediaPhoto(media=image["url"], caption=caption))

        async with ChatActionSender(bot=bot, action=ChatAction.UPLOAD_PHOTO, chat_id=chat_id):
            await callback.message.answer_media_group(media=media)
        await send_vpn_ad(chat_id)
    except Exception:
        await send_error(chat_id=chat_id, text="Ошибка при отправке изображений.")
        raise


# ---------------------------------------------------------------------------
# Обработка Аудио
# ---------------------------------------------------------------------------
@router.callback_query(F.data.startswith("audio"))
async def handle_audio(callback: CallbackQuery) -> AnswerCallbackQuery:
    chat_id = callback.message.chat.id
    await callback.answer()

    if user_activity_queue.get_download(chat_id=chat_id):
        await send_error(chat_id=chat_id, text=MessageTemplates.CALLBACK_PROCESSING_MESSAGE)
        return

    session = user_session_storage.get_session(chat_id=chat_id)
    if session is None:
        await send_error(chat_id=chat_id, text=MessageTemplates.CALLBACK_SESSION_EXPIRED)
        return

    try:
        user_activity_queue.create_download(url=session["url"], chat_id=chat_id, service=session["service"])
        _, audio_id = callback.data.split(":", 1)
        audio = next((a for a in session["media_data"]["audios"] if a.get("id") == audio_id), None)

        if not audio:
            await send_error(chat_id=chat_id, text="Не нашла выбранный формат аудио. Отправь ссылку заново.")
            return

        audio_name = _safe_media_id(audio)
        if not audio_name:
            await send_error(chat_id=chat_id, text="У аудио нет идентификатора (name/id).")
            return

        if audio_name == "music":
            download_audio.delay(
                direct=True, url=audio.get("url"), audio_id=audio_name,
                service=session["service"], chat_id=chat_id, message_id=callback.message.message_id,
                original_url=session["url"]
            )
        elif audio_name == "bestaudio":
            download_audio.delay(
                direct=False, url=audio.get("url"), audio_id="bestaudio",
                service=session["service"], chat_id=chat_id, message_id=callback.message.message_id,
                original_url=session["url"]
            )
        else:
            download_audio.delay(
                direct=False, url=session["url"], audio_id=audio_name,
                service=session["service"], chat_id=chat_id, message_id=callback.message.message_id
            )

    except Exception:
        await send_error(chat_id=chat_id, text="Ошибка при обработке аудио. Попробуй ещё раз.")
        raise
    finally:
        try:
            user_activity_queue.delete_download(chat_id=chat_id)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Превью и Подписки
# ---------------------------------------------------------------------------
@router.callback_query(F.data.startswith("thumbnail"))
async def handle_thumbnail(callback: CallbackQuery) -> AnswerCallbackQuery:
    chat_id = callback.message.chat.id
    await callback.answer()

    session = user_session_storage.get_session(chat_id=chat_id)
    if session is None:
        await send_error(chat_id=chat_id, text=MessageTemplates.CALLBACK_SESSION_EXPIRED)
        return

    try:
        _, thumbnail_id = callback.data.split(":", 1)
        thumbnail = next((t for t in session["media_data"]["thumbnails"] if t.get("id") == thumbnail_id), None)

        if not thumbnail:
            await send_error(chat_id=chat_id, text="Не нашла превью. Отправь ссылку заново.")
            return

        caption = MessageTemplates.CALLBACK_THUMBNAIL_CAPTION.format(
            url=session["url"], service=session["service"], botname=settings.telegram.name,
            author_name=session["media_data"].get("author_name", "Unknown")
        )

        async with ChatActionSender(bot=bot, action=ChatAction.UPLOAD_PHOTO, chat_id=chat_id):
            await callback.message.answer_photo(photo=thumbnail.get("url"), caption=caption)
    except Exception:
        await send_error(chat_id=chat_id, text="Ошибка при отправке превью.")
        raise


@router.callback_query(F.data == "check_subscription")
async def check_subscription_callback(callback: CallbackQuery):
    user_id = callback.from_user.id
    await callback.answer()

    missing_subscriptions = []
    for channel_username in settings.telegram.subscription_channels:
        clean_username = channel_username.lstrip("@")
        try:
            chat = await callback.bot.get_chat(f"@{clean_username}")
            member = await callback.bot.get_chat_member(chat_id=chat.id, user_id=user_id)
            if member.status not in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR):
                missing_subscriptions.append({"chat_id": chat.id, "username": f"@{clean_username}",
                                              "title": getattr(chat, "title", f"@{clean_username}")})
        except Exception:
            missing_subscriptions.append(
                {"chat_id": None, "username": f"@{clean_username}", "title": f"@{clean_username}"})

    if not missing_subscriptions:
        await callback.message.edit_text(text=MessageTemplates.SUBSCRIPTION_SUCCESS)
        await callback.answer(text=MessageTemplates.SUBSCRIPTION)
        return

    reply_markup = get_retry_subscription_keyboard(missing_subscriptions=missing_subscriptions)
    text = MessageTemplates.SUBSCRIPTION_ERROR_ONE.format(channel_name=missing_subscriptions[0]["title"]) if len(
        missing_subscriptions) == 1 else MessageTemplates.SUBSCRIPTION_ERROR_MANY.format(
        channel_list="\n".join([f"• {ch['title']}" for ch in missing_subscriptions]))

    try:
        await callback.message.edit_text(text=text, reply_markup=reply_markup)
    except Exception:
        pass
    await callback.answer(text=MessageTemplates.NOT_SUBSCRIPTION)