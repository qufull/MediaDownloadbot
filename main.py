import asyncio
import logging

from src.bot.app import start_bot
from src.core.youtube_healthcheck import run_youtube_healthcheck
from src.utils.logging_config import setup_logging


async def main() -> None:
    setup_logging(level=logging.INFO)
    # Startup self-test: youtube_stack=healthy/unhealthy
    run_youtube_healthcheck()
    from src.core.youtube_healthcheck import _log_drive_status
    _log_drive_status()
    await start_bot()
    

if __name__ == "__main__":
    asyncio.run(main())
