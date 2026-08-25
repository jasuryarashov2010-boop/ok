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
from app.handlers.user import router as user_router
from app.handlers.admin import router as admin_router
from app.worker import broadcast_loop


app = FastAPI(title="Stars & Gifts Commerce Bot")

bot: Bot | None = None
dispatcher: Dispatcher | None = None

bot_task: asyncio.Task | None = None
broadcast_task: asyncio.Task | None = None


# ============================================================
# TELEGRAM BOT
# ============================================================

async def start_telegram() -> None:
    """
    Telegram botni polling rejimida ishga tushiradi.

    Muhim:
    - webhook o'chiriladi
    - bot o'zini get_me() orqali tekshiradi
    - routerlar ulanadi
    - polling boshlanadi
    - xatolar log qilinadi
    """

    global bot, dispatcher

    try:
        print("========================================", flush=True)
        print("🚀 TELEGRAM BOT STARTING...", flush=True)
        print("========================================", flush=True)

        # --------------------------------------------
        # Bot yaratish
        # --------------------------------------------
        bot = Bot(
            token=settings.bot_token,
        )

        # --------------------------------------------
        # Telegram API bilan autentifikatsiya
        # --------------------------------------------
        me = await bot.get_me()

        print(
            f"✅ TELEGRAM AUTHENTICATED",
            flush=True,
        )

        print(
            f"🤖 Bot username: @{me.username}",
            flush=True,
        )

        print(
            f"🆔 Bot ID: {me.id}",
            flush=True,
        )

        # --------------------------------------------
        # Dispatcher
        # --------------------------------------------
        dispatcher = Dispatcher()

        # Admin router
        dispatcher.include_router(admin_router)

        # User router
        dispatcher.include_router(user_router)

        print(
            "✅ ADMIN ROUTER LOADED",
            flush=True,
        )

        print(
            "✅ USER ROUTER LOADED",
            flush=True,
        )

        # --------------------------------------------
        # Bot commands
        # --------------------------------------------
        await bot.set_my_commands(
            [
                BotCommand(
                    command="start",
                    description="Botni ishga tushirish",
                ),
                BotCommand(
                    command="admin",
                    description="Admin panel",
                ),
            ]
        )

        print(
            "✅ BOT COMMANDS REGISTERED",
            flush=True,
        )

        # --------------------------------------------
        # Webhookni to'liq o'chirish
        # --------------------------------------------
        webhook_info = await bot.get_webhook_info()

        print(
            f"ℹ️ CURRENT WEBHOOK: {webhook_info.url or 'NONE'}",
            flush=True,
        )

        await bot.delete_webhook(
            drop_pending_updates=False
        )

        print(
            "✅ WEBHOOK REMOVED",
            flush=True,
        )

        # --------------------------------------------
        # Polling
        # --------------------------------------------
        print(
            "🚀 TELEGRAM POLLING STARTING...",
            flush=True,
        )

        await dispatcher.start_polling(
            bot,
            handle_signals=False,
            allowed_updates=dispatcher.resolve_used_update_types(),
        )

    except asyncio.CancelledError:
        print(
            "🛑 TELEGRAM POLLING CANCELLED",
            flush=True,
        )
        raise

    except Exception as exc:
        print(
            "🚨 TELEGRAM POLLING CRASHED",
            flush=True,
        )

        print(
            f"ERROR: {exc!r}",
            flush=True,
        )

        traceback.print_exc()

        raise


# ============================================================
# BROADCAST WORKER
# ============================================================

async def _broadcast_runner() -> None:
    """
    Broadcast queue uchun Redis worker.
    """
    print(
        "📣 BROADCAST WORKER STARTING...",
        flush=True,
    )

    try:
        await broadcast_loop()

    except asyncio.CancelledError:
        print(
            "🛑 BROADCAST WORKER CANCELLED",
            flush=True,
        )
        raise

    except Exception as exc:
        print(
            f"🚨 BROADCAST WORKER CRASHED: {exc!r}",
            flush=True,
        )

        traceback.print_exc()

        raise


# ============================================================
# STARTUP
# ============================================================

@app.on_event("startup")
async def startup_event() -> None:
    global bot_task
    global broadcast_task

    print("========================================", flush=True)
    print("🚀 APPLICATION STARTUP", flush=True)
    print("========================================", flush=True)

    # --------------------------------------------
    # Telegram polling task
    # --------------------------------------------
    bot_task = asyncio.create_task(
        start_telegram(),
        name="telegram-polling",
    )

    def telegram_done(task: asyncio.Task) -> None:
        try:
            task.result()

        except asyncio.CancelledError:
            print(
                "🛑 Telegram task cancelled.",
                flush=True,
            )

        except Exception as exc:
            print(
                "🚨 Telegram task terminated!",
                flush=True,
            )

            print(
                f"ERROR: {exc!r}",
                flush=True,
            )

            traceback.print_exc()

    bot_task.add_done_callback(
        telegram_done
    )

    # --------------------------------------------
    # Broadcast worker
    # --------------------------------------------
    broadcast_task = asyncio.create_task(
        _broadcast_runner(),
        name="broadcast-worker",
    )

    def broadcast_done(task: asyncio.Task) -> None:
        try:
            task.result()

        except asyncio.CancelledError:
            print(
                "🛑 Broadcast task cancelled.",
                flush=True,
            )

        except Exception as exc:
            print(
                "🚨 Broadcast task terminated!",
                flush=True,
            )

            print(
                f"ERROR: {exc!r}",
                flush=True,
            )

            traceback.print_exc()

    broadcast_task.add_done_callback(
        broadcast_done
    )

    print(
        "✅ STARTUP TASKS CREATED",
        flush=True,
    )


# ============================================================
# SHUTDOWN
# ============================================================

@app.on_event("shutdown")
async def shutdown_event() -> None:
    global bot_task
    global broadcast_task
    global bot

    print("========================================", flush=True)
    print("🛑 APPLICATION SHUTDOWN", flush=True)
    print("========================================", flush=True)

    # --------------------------------------------
    # Cancel Telegram polling
    # --------------------------------------------
    if bot_task:
        bot_task.cancel()

    # --------------------------------------------
    # Cancel broadcast worker
    # --------------------------------------------
    if broadcast_task:
        broadcast_task.cancel()

    # --------------------------------------------
    # Wait Telegram task
    # --------------------------------------------
    if bot_task:
        try:
            await bot_task

        except asyncio.CancelledError:
            pass

        except Exception as exc:
            print(
                f"Shutdown Telegram error: {exc!r}",
                flush=True,
            )

    # --------------------------------------------
    # Wait broadcast task
    # --------------------------------------------
    if broadcast_task:
        try:
            await broadcast_task

        except asyncio.CancelledError:
            pass

        except Exception as exc:
            print(
                f"Shutdown Broadcast error: {exc!r}",
                flush=True,
            )

    # --------------------------------------------
    # Close Telegram session
    # --------------------------------------------
    if bot:
        try:
            await bot.session.close()
        except Exception as exc:
            print(
                f"Bot session close error: {exc!r}",
                flush=True,
            )

        bot = None

    # --------------------------------------------
    # Close Redis
    # --------------------------------------------
    try:
        await redis.close()
    except Exception as exc:
        print(
            f"Redis close error: {exc!r}",
            flush=True,
        )

    print(
        "✅ APPLICATION SHUTDOWN COMPLETE",
        flush=True,
    )


# ============================================================
# ROOT
# ============================================================

@app.get("/")
async def root():
    return {
        "service": settings.bot_name,
        "status": "running",
        "telegram": (
            "running"
            if bot is not None
            else "starting"
        ),
    }


@app.head("/")
async def head_root():
    return None


# ============================================================
# HEALTH CHECK
# ============================================================

@app.get("/health")
async def health():
    db_status = "ok"
    redis_status = "ok"

    # --------------------------------------------
    # PostgreSQL
    # --------------------------------------------
    try:
        async with engine.connect() as conn:
            await conn.execute(
                text("SELECT 1")
            )

    except Exception as exc:
        db_status = (
            f"error:{type(exc).__name__}"
        )

    # --------------------------------------------
    # Redis
    # --------------------------------------------
    try:
        await redis.ping()

    except Exception as exc:
        redis_status = (
            f"error:{type(exc).__name__}"
        )

    # --------------------------------------------
    # Telegram
    # --------------------------------------------
    telegram_status = (
        "running"
        if bot is not None
        else "starting"
    )

    if (
        db_status == "ok"
        and redis_status == "ok"
        and telegram_status == "running"
    ):
        status = "ok"
    else:
        status = "degraded"

    return {
        "status": status,
        "database": db_status,
        "redis": redis_status,
        "telegram_bot": telegram_status,
    }
