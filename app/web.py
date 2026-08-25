from __future__ import annotations

import asyncio

from fastapi import FastAPI
from sqlalchemy import text

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand

from app.config import settings
from app.db import engine
from app.services.redis_service import redis
from app.services.redis_service import pop_job
from app.handlers.user import router as user_router
from app.handlers.admin import router as admin_router
from app.worker import broadcast_loop

app = FastAPI(title="Stars & Gifts Commerce Bot")

bot: Bot | None = None
dispatcher: Dispatcher | None = None
bot_task: asyncio.Task | None = None
broadcast_task: asyncio.Task | None = None


async def start_telegram() -> None:
    global bot, dispatcher

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

    # Polling and webhook must not be used together.
    await bot.delete_webhook(drop_pending_updates=False)

    print("INFO: Telegram polling starting...", flush=True)
    await dispatcher.start_polling(bot, handle_signals=False)
    
@app.on_event("startup")
async def startup_event() -> None:
    global bot_task, broadcast_task

    bot_task = asyncio.create_task(
        start_telegram(),
        name="telegram-polling",
    )

    def telegram_done(task: asyncio.Task):
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            import traceback
            print("🚨 TELEGRAM POLLING CRASHED:", repr(exc), flush=True)
            traceback.print_exc()

    bot_task.add_done_callback(telegram_done)

    broadcast_task = asyncio.create_task(
        _broadcast_runner(),
        name="broadcast-worker",
    )
@app.on_event("shutdown")
async def shutdown_event() -> None:
    global bot_task, broadcast_task, bot

    for task in (bot_task, broadcast_task):
        if task:
            task.cancel()

    for task in (bot_task, broadcast_task):
        if task:
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                print(f"Shutdown task error: {exc}", flush=True)

    if bot:
        await bot.session.close()
        bot = None

    try:
        await redis.close()
    except Exception:
        pass


@app.get("/")
async def root():
    return {"service": settings.bot_name, "status": "running", "telegram": "starting" if bot is None else "running"}


@app.head("/")
async def head_root():
    return None


@app.get("/health")
async def health():
    db = "ok"
    rd = "ok"

    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:
        db = f"error:{type(exc).__name__}"

    try:
        await redis.ping()
    except Exception as exc:
        rd = f"error:{type(exc).__name__}"

    tg = "running" if bot else "starting"
    status = "ok" if db == "ok" and rd == "ok" and tg == "running" else "degraded"
    return {"status": status, "database": db, "redis": rd, "telegram_bot": tg}
