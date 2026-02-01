# src/celery_app/tasks/extract.py
import logging
from typing import List

from aiogram.enums import ChatAction
from aiogram.types import FSInputFile
from aiogram.utils.chat_action import ChatActionSender

from src.config import bot
from src.config import settings
from src.core import ResultDictAnnotation, AbstractErrorCodeModel
from src.config import user_activity_queue, media_cache_storage, user_session_storage, file_id_cache

from ..app import celery_app, celery_event_loop
from .texts import MessageTemplates
from .common import MediaProcessor, create_keyboard_layout, get_extractor, get_inline_keyboard
from src.utils.telegram_anim import send_error

logger = logging.getLogger(__name__)


async def async_extract_info(chat_id: int, message_id: int, url: str, service: str) -> None:
    logger.info(f"[async_extract_info] Запуск обработки: chat_id={chat_id}, url={url}, service={service}")
    message_ids = [message_id]

    try:
        if user_activity_queue.get_extract(chat_id=chat_id):
            logger.warning(f"[async_extract_info] Уже выполняется операция для chat_id={chat_id}")
            await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
            await bot.send_message(chat_id=chat_id, text=MessageTemplates.PROCESSING_MESSAGE)
            return

        user_activity_queue.create_extract(chat_id=chat_id, url=url, service=service)
        logger.debug(f"[async_extract_info] Запись в очередь пользователя создана: {chat_id}")

        await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        message = await bot.send_message(chat_id=chat_id, text=MessageTemplates.RECEIVED_MESSAGE)
        message_ids.append(message.message_id)
        logger.debug(f"[async_extract_info] Отправлено сообщение о получении запроса: message_id={message.message_id}")

        async with ChatActionSender(bot=bot, chat_id=chat_id, action=ChatAction.TYPING):
            if media := media_cache_storage.get_media(url=url):
                logger.info(f"[async_extract_info] Найдено медиа в кеше (url={url})")
                response = ResultDictAnnotation(
                    context=None,
                    status="success",
                    data=media["data"],
                    code=AbstractErrorCodeModel.SUCCESS.value,
                )
            else:
                logger.info(
                    f"[async_extract_info] Медиа в кеше не найдено, извлечение через extractor (service={service})")
                extractor = get_extractor(service=service)
                response = extractor.extract_info(url=url).to_dict()

            if response["status"] == "success":
                logger.info(f"[async_extract_info] Извлечение успешно для chat_id={chat_id}")
                await handle_success_response(
                    url=url,
                    chat_id=chat_id,
                    service=service,
                    response=response,
                    message_ids=message_ids,
                )

            else:
                logger.error(f"[async_extract_info] Ошибка при извлечении информации (code={response['code']})")
                await handle_error_response(
                    url=url,
                    chat_id=chat_id,
                    service=service,
                    response=response,
                    message_ids=message_ids,
                )

    except Exception as e:
        logger.exception(f"[async_extract_info] Исключение при обработке запроса: {e}")
        await send_error(chat_id=chat_id, text=MessageTemplates.EXTRACT_ERROR.format(code="EXCEPTION"))
    finally:
        user_activity_queue.delete_extract(chat_id=chat_id)
        logger.debug(f"[async_extract_info] Очередь пользователя очищена: chat_id={chat_id}")


async def handle_success_response(
        url: str,
        chat_id: int,
        service: str,
        message_ids: List[int],
        response: ResultDictAnnotation
) -> None:
    logger.info(f"[handle_success_response] Обработка успешного результата: chat_id={chat_id}, service={service}")

    try:
        data = response["data"]

        user_session_storage.create_session(
            url=url,
            chat_id=chat_id,
            service=service,
            media_data=data,
        )

        media_cache_storage.store_media(url=url, media_data=data)
        logger.debug(f"[handle_success_response] Данные сохранены в сессии и кеше (chat_id={chat_id})")

        # 🚀 Получаем закэшированные качества для этого URL
        cached_heights = file_id_cache.get_cached_qualities(url=url)
        if cached_heights:
            logger.info(f"[handle_success_response] Найдены закэшированные качества: {cached_heights}")

        buttons = []
        processor = MediaProcessor()
        preview_url = FSInputFile("src/assets/image_not_found.png")

        # 🚀 Передаём cached_heights в parse_videos
        if video_buttons := processor.parse_videos(data["videos"], cached_heights=cached_heights):
            buttons.extend(video_buttons)

        if audio_button := processor.parse_audios(data["audios"]):
            buttons.append(audio_button)

        if thumbnail_button := processor.parse_thumbnails(data["thumbnails"]):
            buttons.append(thumbnail_button)
            preview_url = thumbnail_button.url

        if image_button := processor.parse_images(data["images"]):
            buttons.append(image_button)
            preview_url = image_button.url

        logger.debug(f"[handle_success_response] Кнопок сформировано: {len(buttons)}")

        keyboard_data = create_keyboard_layout(buttons=buttons)
        inline_keyboard = get_inline_keyboard(data=keyboard_data)
        caption = MessageTemplates.EXTRACT_CAPTION.format(
            service=service,
            author_name=data["author_name"],
            title=data["title"][:35],
            url=url,
            botname=settings.telegram.name,
        )

        try:
            await bot.delete_messages(chat_id=chat_id, message_ids=message_ids)
        except Exception as e:
            logger.warning(f"[handle_success_response] Ошибка удаления сообщений: {e}")

        async with ChatActionSender(bot=bot, chat_id=chat_id, action=ChatAction.UPLOAD_PHOTO):
            await bot.send_photo(
                chat_id=chat_id,
                caption=caption,
                photo=preview_url,
                reply_markup=inline_keyboard,
            )
        logger.info(f"[handle_success_response] Медиа успешно отправлено пользователю chat_id={chat_id}")

    except Exception as e:
        logger.exception(f"[handle_success_response] Ошибка при обработке успешного результата: {e}")
        await send_error(chat_id=chat_id, text=MessageTemplates.EXTRACT_ERROR.format(code="SUCCESS_HANDLER_ERROR"))


async def handle_error_response(
        url: str,
        service: str,
        chat_id: int,
        message_ids: List[int],
        response: ResultDictAnnotation
) -> None:
    logger.error(
        f"[handle_error_response] Обработка ошибки: chat_id={chat_id}, code={response.get('code')}, service={service}"
    )

    try:
        try:
            if len(message_ids) > 1:
                await bot.delete_messages(chat_id=chat_id, message_ids=message_ids[1:])
        except Exception as e:
            logger.warning(f"[handle_error_response] Ошибка удаления сообщений: {e}")

        extractor = get_extractor(service=service)
        code = response.get("code")
        code_text = extractor.get_error_description(code)

        await send_error(
            chat_id=chat_id,
            text=MessageTemplates.EXTRACT_ERROR.format(code=code_text),
        )

        logger.info(f"[handle_error_response] Гифка ошибки отправлена пользователю chat_id={chat_id}")

    except Exception as e:
        logger.exception(f"[handle_error_response] Ошибка при отправке гифки ошибки: {e}")
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=MessageTemplates.EXTRACT_ERROR.format(code="ERROR_HANDLER_EXCEPTION"),
            )
        except Exception:
            pass


@celery_app.task(name="extract_info", queue="extract_queue")
def extract_info(chat_id: int, message_id: int, url: str, service: str) -> None:
    logger.info(f"[extract_info] Celery-задача запущена: chat_id={chat_id}, service={service}, url={url}")
    try:
        celery_event_loop.run_until_complete(
            async_extract_info(
                url=url,
                service=service,
                chat_id=chat_id,
                message_id=message_id,
            )
        )
        logger.info(f"[extract_info] Celery-задача завершена успешно: chat_id={chat_id}")
    except Exception as e:
        logger.exception(f"[extract_info] Ошибка выполнения celery-задачи: {e}")