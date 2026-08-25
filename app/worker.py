from __future__ import annotations
import asyncio,json
from aiogram import Bot,Dispatcher
from aiogram.types import BotCommand
from app.config import settings
from app.handlers.user import router as user_router
from app.handlers.admin import router as admin_router
from app.services.redis_service import redis
from app.db import Session,User,Broadcast
from sqlalchemy import select,desc

async def broadcast_loop(bot:Bot):
    while True:
        item=await redis.blpop('broadcast:queue',timeout=5)
        if not item:continue
        try:bid=int(json.loads(item[1]).get('id'))
        except:continue
        async with Session() as s:
            b=await s.get(Broadcast,bid)
            if not b:continue
            b.status='SENDING';await s.commit()
            q=select(User).where(User.is_blocked==False)
            if b.segment=='NEW': q=q.where(User.lifetime_spent==0)
            elif b.segment=='BUYERS': q=q.where(User.lifetime_spent>0)
            elif b.segment=='VIP': q=q.where(User.account_status=='VIP')
            users=(await s.execute(q)).scalars().all()
        sent=failed=0
        for u in users:
            try:await bot.send_message(u.id,b.text,parse_mode='HTML');sent+=1
            except Exception:failed+=1
            await asyncio.sleep(0.05)
        async with Session() as s:
            b=await s.get(Broadcast,bid)
            if b:b.status='COMPLETED';b.sent_count=sent;b.failed_count=failed;await s.commit()

async def run():
    bot=Bot(settings.bot_token);dp=Dispatcher();dp.include_router(admin_router);dp.include_router(user_router)
    await bot.set_my_commands([BotCommand(command='start',description='Botni ishga tushirish'),BotCommand(command='admin',description='Admin panel')])
    await bot.delete_webhook(drop_pending_updates=True)
    await asyncio.gather(dp.start_polling(bot),broadcast_loop(bot))

if __name__=='__main__':asyncio.run(run())
