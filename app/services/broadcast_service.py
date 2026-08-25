from __future__ import annotations

import asyncio
import json

from aiogram import Bot
from sqlalchemy import select

from app.db import Broadcast, Session, User
from app.services.redis_service import redis


async def broadcast_loop(bot: Bot) -> None:
    """Process queued broadcasts from Redis without importing app.worker."""
    while True:
        item = await redis.blpop("broadcast:queue", timeout=5)
        if not item:
            continue

        try:
            payload = json.loads(item[1])
            broadcast_id = int(payload["id"])
        except (ValueError, TypeError, KeyError, json.JSONDecodeError):
            continue

        async with Session() as session:
            broadcast = await session.get(Broadcast, broadcast_id)
            if broadcast is None:
                continue

            broadcast.status = "SENDING"
            await session.commit()

            query = select(User).where(User.is_blocked.is_(False))
            if broadcast.segment == "NEW":
                query = query.where(User.lifetime_spent == 0)
            elif broadcast.segment == "BUYERS":
                query = query.where(User.lifetime_spent > 0)
            elif broadcast.segment == "VIP":
                query = query.where(User.account_status == "VIP")

            users = (await session.execute(query)).scalars().all()

        sent = 0
        failed = 0

        for user in users:
            try:
                await bot.send_message(
                    user.id,
                    broadcast.text,
                    parse_mode="HTML",
                )
                sent += 1
            except Exception:
                failed += 1
            await asyncio.sleep(0.05)

        async with Session() as session:
            broadcast = await session.get(Broadcast, broadcast_id)
            if broadcast is not None:
                broadcast.status = "COMPLETED"
                broadcast.sent_count = sent
                broadcast.failed_count = failed
                await session.commit()
