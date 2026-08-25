from decimal import Decimal
from sqlalchemy import select
from app.db import Setting, Session
from app.config import settings

async def get_value(key, default=None):
    async with Session() as s:
        row=await s.get(Setting,key)
        return row.value if row else default
async def get_decimal(key,default=Decimal('0')):
    try:return Decimal(str(await get_value(key,str(default))))
    except:return default
async def get_bool(key,default=False):
    return str(await get_value(key,str(default))).lower() in {'1','true','yes','on'}
async def set_value(key,value):
    async with Session() as s:
        row=await s.get(Setting,key)
        if row: row.value=str(value)
        else: s.add(Setting(key=key,value=str(value)))
        await s.commit()
