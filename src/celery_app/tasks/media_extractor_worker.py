# src/celery_app/tasks/media_extractor_worker.py
import asyncio
import logging
from typing import List

from aiogram.enums import ChatAction
from aiogram.types import FSInputFile
from aiogram.utils.chat_action import ChatActionSender

from src.celery_app.app import shared_loop
from src.config import bot, user_registry, settings
from src.config import (
    user_activity_queue,
    media_cache_storage,
    user_session_storage,
    file_id_cache,
)
from src.core import ResultDictAnnotation, AbstractErrorCodeModel
from src.databases.user_activity_queue import MAX_QUEUE_SIZE

from ..app import celery_app
from .texts import MessageTemplates
from .common import MediaProcessor, create_keyboard_layout, get_extractor, get_inline_keyboard
from src.utils.telegram_anim import send_error

logger = logging.getLogger(__name__)


async def _process_one(chat_id: int, url: str, service: str, origin_message_id: int) -> None:
    """Обработать одну ссылку: извлечь метаданные и отправить карточку."""
    logger.info("[_process_one] chat_id=%s service=%s url=%s", chat_id, service, url)
    message_ids = [origin_message_id]

    try:
        await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        status_msg = await bot.send_message(chat_id=chat_id, text=MessageTemplates.RECEIVED_MESSAGE)
        message_ids.append(status_msg.message_id)

        async with ChatActionSender(bot=bot, chat_id=chat_id, action=ChatAction.TYPING):
            if cached := media_cache_storage.get_media(url=url):
                logger.info("[_process_one] Кэш-хит для url=%s", url)
                response = {
                    "context": None,
                    "status": "success",
                    "data": cached["data"],
                    "code": AbstractErrorCodeModel.SUCCESS.value,
                }
            else:
                extractor = get_extractor(service=service)
                response = extractor.extract_info(url=url).to_dict()

        if response["status"] == "success":
            await _handle_success(
                url=url, chat_id=chat_id, service=service,
                response=response, message_ids=message_ids,
            )
        else:
            await _handle_error(
                url=url, chat_id=chat_id, service=service,
                response=response, message_ids=message_ids,
            )

    except Exception as e:
        logger.exception("[_process_one] Исключение chat_id=%s: %s", chat_id, e)
        await send_error(chat_id=chat_id, text=MessageTemplates.EXTRACT_ERROR.format(code="EXCEPTION"))


async def _drain_queue(chat_id: int) -> None:
    """Последовательно обработать все ссылки в очереди пользователя."""
    logger.info("[_drain_queue] Старт для chat_id=%s", chat_id)

    while True:
        item = user_activity_queue.pop_url(chat_id=chat_id)
        if item is None:
            break

        url     = item["url"]
        service = item["service"]
        user_activity_queue.set_processing(chat_id=chat_id, url=url, service=service)

        remaining = user_activity_queue.queue_size(chat_id=chat_id)
        if remaining > 0:
            await bot.send_message(
                chat_id=chat_id,
                text=MessageTemplates.QUEUE_PROCESSING.format(position=1, remaining=remaining),
            )

        await _process_one(chat_id=chat_id, url=url, service=service, origin_message_id=0)

    user_activity_queue.clear_processing(chat_id=chat_id)
    logger.info("[_drain_queue] Очередь chat_id=%s исчерпана", chat_id)


async def async_extract_info(chat_id: int, message_id: int, url: str, service: str) -> None:
    """
    Точка входа для Celery-задачи.

    1. Добавляем ссылку в очередь (макс. MAX_QUEUE_SIZE).
    2. Если очередь была пуста и нет активной обработки — запускаем дренаж.
    3. Если уже идёт обработка — сообщаем позицию в очереди и выходим.
    """
    logger.info("[async_extract_info] chat_id=%s url=%s service=%s", chat_id, url, service)

    was_processing = user_activity_queue.is_processing(chat_id=chat_id)
    queue_before   = user_activity_queue.queue_size(chat_id=chat_id)

    accepted = user_activity_queue.push_url(chat_id=chat_id, url=url, service=service)

    if not accepted:
        await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        await bot.send_message(
            chat_id=chat_id,
            text=MessageTemplates.QUEUE_FULL.format(max_size=MAX_QUEUE_SIZE),
        )
        logger.warning("[async_extract_info] Очередь переполнена для chat_id=%s", chat_id)
        return

    queue_after = user_activity_queue.queue_size(chat_id=chat_id)

    if was_processing or queue_before > 0:
        await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        await bot.send_message(
            chat_id=chat_id,
            text=MessageTemplates.QUEUE_ADDED.format(
                position=queue_after,
                max_size=MAX_QUEUE_SIZE,
            ),
        )
        logger.info("[async_extract_info] URL в очередь позиция=%s chat_id=%s", queue_after, chat_id)
        return

    await _drain_queue(chat_id=chat_id)


async def _handle_success(
    url: str,
    chat_id: int,
    service: str,
    message_ids: List[int],
    response: dict,
) -> None:
    logger.info("[_handle_success] chat_id=%s service=%s", chat_id, service)
    try:
        data = response["data"]

        user_session_storage.create_session(
            url=url, chat_id=chat_id, service=service, media_data=data,
        )
        media_cache_storage.store_media(url=url, media_data=data)

        cached_heights = file_id_cache.get_cached_qualities(url=url)
        buttons = []
        processor = MediaProcessor()
        preview_url = FSInputFile("src/assets/image_not_found.png")
        is_premium = user_registry.is_user_premium(chat_id) or chat_id in settings.telegram.admin_ids

        if video_buttons := processor.parse_videos(
            data["videos"], cached_heights=cached_heights, service=service, is_premium=is_premium
        ):
            buttons.extend(video_buttons)

        if audio_button := processor.parse_audios(data["audios"]):
            buttons.append(audio_button)

        if thumbnail_button := processor.parse_thumbnails(data["thumbnails"]):
            buttons.append(thumbnail_button)
            preview_url = thumbnail_button.url

        if image_button := processor.parse_images(data["images"]):
            buttons.append(image_button)
            preview_url = image_button.url

        keyboard_data   = create_keyboard_layout(buttons=buttons)
        inline_keyboard = get_inline_keyboard(data=keyboard_data)

        caption = MessageTemplates.EXTRACT_CAPTION.format(
            service=service,
            author_name=data["author_name"],
            title=(data["title"] or "")[:35],
            url=url,
            botname=settings.telegram.name,
        )

        try:
            ids_to_delete = [mid for mid in message_ids if mid != 0]
            if ids_to_delete:
                await bot.delete_messages(chat_id=chat_id, message_ids=ids_to_delete)
        except Exception as e:
            logger.warning("[_handle_success] Ошибка удаления сообщений: %s", e)

        async with ChatActionSender(bot=bot, chat_id=chat_id, action=ChatAction.UPLOAD_PHOTO):
            await bot.send_photo(
                chat_id=chat_id,
                caption=caption,
                photo=preview_url,
                reply_markup=inline_keyboard,
            )

        remaining = user_activity_queue.queue_size(chat_id=chat_id)
        if remaining > 0:
            await bot.send_message(
                chat_id=chat_id,
                text=MessageTemplates.QUEUE_NEXT.format(remaining=remaining),
            )

    except Exception as e:
        logger.exception("[_handle_success] Ошибка: %s", e)
        await send_error(chat_id=chat_id, text=MessageTemplates.EXTRACT_ERROR.format(code="SUCCESS_HANDLER_ERROR"))


async def _handle_error(
    url: str,
    service: str,
    chat_id: int,
    message_ids: List[int],
    response: dict,
) -> None:
    logger.error("[_handle_error] chat_id=%s code=%s", chat_id, response.get("code"))
    try:
        ids_to_delete = [mid for mid in message_ids[1:] if mid != 0]
        if ids_to_delete:
            await bot.delete_messages(chat_id=chat_id, message_ids=ids_to_delete)
    except Exception as e:
        logger.warning("[_handle_error] Ошибка удаления сообщений: %s", e)

    try:
        extractor = get_extractor(service=service)
        code_text = extractor.get_error_description(response.get("code"))
        await send_error(chat_id=chat_id, text=MessageTemplates.EXTRACT_ERROR.format(code=code_text))
    except Exception as e:
        logger.exception("[_handle_error] Ошибка отправки: %s", e)
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=MessageTemplates.EXTRACT_ERROR.format(code="ERROR_HANDLER_EXCEPTION"),
            )
        except Exception:
            pass


@celery_app.task(name="extract_info", queue="extract_queue")
def extract_info(chat_id: int, message_id: int, url: str, service: str) -> None:
    logger.info("[extract_info] Celery-задача: chat_id=%s service=%s url=%s", chat_id, service, url)
    try:
        future = asyncio.run_coroutine_threadsafe(
            async_extract_info(
                url=url, service=service,
                chat_id=chat_id, message_id=message_id,
            ),
            shared_loop,
        )
        future.result()
        logger.info("[extract_info] Celery-задача завершена: chat_id=%s", chat_id)
    except Exception as e:
        logger.exception("[extract_info] Ошибка выполнения: %s", e)