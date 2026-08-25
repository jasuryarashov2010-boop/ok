from __future__ import annotations

import asyncio
import traceback

from fastapi import FastAPI
from sqlalchemy import text

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand

from app.config import settings
from app.db import engine
from app.services.redis_service import redis
from app.services.broadcast_service import broadcast_loop
from app.handlers.user import router as user_router
from app.handlers.admin import router as admin_router


app = FastAPI(title="Stars & Gifts Commerce Bot")

bot: Bot | None = None
dispatcher: Dispatcher | None = None
bot_task: asyncio.Task | None = None
broadcast_task: asyncio.Task | None = None


async def start_telegram() -> None:
    global bot, dispatcher

    try:
        print("🚀 TELEGRAM BOT STARTING...", flush=True)

        bot = Bot(settings.bot_token)
        me = await bot.get_me()
        print(f"✅ TELEGRAM AUTHENTICATED: @{me.username} ({me.id})", flush=True)

        dispatcher = Dispatcher()
        dispatcher.include_router(admin_router)
        dispatcher.include_router(user_router)
        print("✅ ADMIN ROUTER LOADED", flush=True)
        print("✅ USER ROUTER LOADED", flush=True)

        await bot.set_my_commands(
            [
                BotCommand(command="start", description="Botni ishga tushirish"),
                BotCommand(command="admin", description="Admin panel"),
            ]
        )

        webhook = await bot.get_webhook_info()
        print(f"ℹ️ WEBHOOK: {webhook.url or 'NONE'}", flush=True)
        await bot.delete_webhook(drop_pending_updates=False)
        print("✅ WEBHOOK REMOVED", flush=True)

        print("🚀 TELEGRAM POLLING STARTING...", flush=True)
        await dispatcher.start_polling(
            bot,
            handle_signals=False,
            allowed_updates=dispatcher.resolve_used_update_types(),
        )
    except asyncio.CancelledError:
        print("🛑 TELEGRAM POLLING CANCELLED", flush=True)
        raise
    except Exception as exc:
        print(f"🚨 TELEGRAM POLLING CRASHED: {exc!r}", flush=True)
        traceback.print_exc()
        raise


async def _broadcast_runner() -> None:
    while bot is None:
        await asyncio.sleep(0.2)
    await broadcast_loop(bot)


@app.on_event("startup")
async def startup_event() -> None:
    global bot_task, broadcast_task

    bot_task = asyncio.create_task(start_telegram(), name="telegram-polling")
    broadcast_task = asyncio.create_task(_broadcast_runner(), name="broadcast-worker")
    print("✅ STARTUP TASKS CREATED", flush=True)


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
                print(f"Shutdown task error: {exc!r}", flush=True)

    if bot:
        await bot.session.close()
        bot = None

    try:
        await redis.close()
    except Exception:
        pass


@app.get("/")
async def root():
    return {
        "service": settings.bot_name,
        "status": "running",
        "telegram": "running" if bot else "starting",
    }


@app.head("/")
async def head_root():
    return None


@app.get("/health")
async def health():
    db_status = "ok"
    redis_status = "ok"

    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:
        db_status = f"error:{type(exc).__name__}"

    try:
        await redis.ping()
    except Exception as exc:
        redis_status = f"error:{type(exc).__name__}"

    telegram_status = "running" if bot else "starting"
    status = (
        "ok"
        if db_status == "ok"
        and redis_status == "ok"
        and telegram_status == "running"
        else "degraded"
    )

    return {
        "status": status,
        "database": db_status,
        "redis": redis_status,
        "telegram_bot": telegram_status,
    }
