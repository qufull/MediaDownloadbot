import asyncio
import logging

from aiogram.types import BotCommand

from src.config import bot, dp, settings
from .middleware import IsAdminMiddleware, UserRegistrationMiddleware

from .handlers import router as handler_router
from .callback_handlers import router as callback_handler_router

logger = logging.getLogger("bot")


async def set_bot_commands() -> None:

    try:
        commands = [
            BotCommand(command="start", description="🚀 Начать работу с ботом"),
            BotCommand(command="help", description="❓ Помощь"),
            BotCommand(command="support", description="🛠 Написать в поддержку"),
            BotCommand(command="ref", description="🎁 Пригласить друга"),
            BotCommand(command="donate", description="💜 Поддержать проект"),
            BotCommand(command="products", description="📦 Другие продукты")
        ]
        await bot.set_my_commands(commands=commands)
    except Exception as e:
            logger.warning(f"Не удалось установить команды: {e}")

def setup_routers() -> None:
    logger.info("🔄 Настройка роутеров...")

    dp.include_routers(
        handler_router,
        callback_handler_router,
    )

    logger.info("✅ Роутеры успешно настроены")


def setup_middleware() -> None:
    logger.info("🔄 Настройка middleware...")

    dp.message.middleware.register(UserRegistrationMiddleware())
    dp.callback_query.middleware.register(UserRegistrationMiddleware())
    dp.message.middleware.register(IsAdminMiddleware())

    logger.info("✅ Middleware успешно настроены")


async def on_startup() -> None:
    logger.info("🤖 Бот запускается...")
    logger.info(f"👤 Имя бота: {settings.telegram.name}")
    logger.info(f"🔧 Режим: {settings.telegram.server_url}")
    logger.info("✅ Бот успешно запущен!")


async def on_shutdown() -> None:
    logger.info("🛑 Бот останавливается...")

    try:
        await bot.session.close()
        logger.debug("Сессия бота успешно закрыта")
    except Exception as e:
        logger.error(f"Ошибка при закрытии сессии: {e}")

    logger.info("👋 Бот успешно остановлен!")


async def start_bot() -> None:
    try:
        logger.info("🎯 Инициализация запуска бота...")

        setup_routers()
        setup_middleware()

        await set_bot_commands()
        await on_startup()

        logger.info("🔄 Запуск поллинга...")
        await dp.start_polling(
            bot,
            skip_updates=True,
            allowed_updates=dp.resolve_used_update_types(),
        )

        logger.info("📡 Поллинг завершен")

    except asyncio.CancelledError:
        logger.warning("Задача бота была отменена")
        raise

    except Exception as e:
        logger.critical(f"❌ Критическая ошибка при работе бота: {e}", exc_info=True)
        raise

    finally:
        await on_shutdown()