from __future__ import annotations

import asyncio

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand

from app.config import settings
from app.handlers.user import router as user_router
from app.handlers.admin import router as admin_router
from app.services.broadcast_service import broadcast_loop


async def run() -> None:
    """Legacy/paid-worker entry point. Not required for the Free web service."""
    bot = Bot(settings.bot_token)
    dispatcher = Dispatcher()
    dispatcher.include_router(admin_router)
    dispatcher.include_router(user_router)

    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Botni ishga tushirish"),
            BotCommand(command="admin", description="Admin panel"),
        ]
    )
    await bot.delete_webhook(drop_pending_updates=True)

    try:
        await asyncio.gather(
            dispatcher.start_polling(bot, handle_signals=False),
            broadcast_loop(bot),
        )
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(run())
