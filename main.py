import asyncio
import logging

from src.bot.app import start_bot


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    await start_bot()
    

if __name__ == "__main__":
    asyncio.run(main())
