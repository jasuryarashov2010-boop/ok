from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from app.config import settings

async def is_subscribed(bot:Bot,user_id:int):
    missing=[]
    for channel in settings.channels:
        try:
            m=await bot.get_chat_member(channel,user_id)
            if m.status in {'left','kicked'} or (m.status=='restricted' and not getattr(m,'is_member',False)): missing.append(channel)
        except TelegramBadRequest: missing.append(channel)
    return not missing,missing

async def send_admin_log(bot:Bot,text:str,disable_notification=True):
    if settings.log_chat_id:
        try: await bot.send_message(settings.log_chat_id,text,parse_mode='HTML',disable_notification=disable_notification)
        except Exception: pass
async def public_feed(bot:Bot,text:str):
    if settings.public_feed_enabled and settings.public_feed_chat_id:
        try: await bot.send_message(settings.public_feed_chat_id,text,parse_mode='HTML',disable_notification=True)
        except Exception: pass
