from __future__ import annotations
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
import json, uuid
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession
from app.db import User, BalanceLedger, CashbackLedger, Order, OrderEvent, Payment, AuditLog, PromoCode, PromoUse, Contest, ContestParticipant, CostLedger, Notification, PriceHistory, Setting

TWOPLACES=Decimal('0.01')

def money(v) -> Decimal:
    return Decimal(str(v)).quantize(TWOPLACES)

def public_id(prefix: str) -> str:
    return f'{prefix}-{datetime.now(timezone.utc):%Y%m%d}-{uuid.uuid4().hex[:8].upper()}'

async def get_or_create_user(session: AsyncSession, tg_user, referred_by: int|None=None) -> User:
    user=await session.get(User,tg_user.id)
    if not user:
        if referred_by==tg_user.id: referred_by=None
        user=User(id=tg_user.id,username=tg_user.username,first_name=tg_user.first_name,last_name=tg_user.last_name,referred_by=referred_by)
        session.add(user); await session.flush()
        if referred_by:
            ref=await session.get(User,referred_by,with_for_update=True)
            if ref:
                ref.referral_count += 1
                active=await session.scalar(select(Contest).where(Contest.active==True,Contest.finished==False).order_by(desc(Contest.id)).limit(1))
                if active:
                    rp=await session.scalar(select(ContestParticipant).where(ContestParticipant.contest_id==active.id,ContestParticipant.user_id==ref.id))
                    if rp: rp.referral_count += 1
                session.add(AuditLog(actor_id=tg_user.id,action='REFERRAL_REGISTERED',entity='user',entity_id=str(tg_user.id),details=json.dumps({'referred_by':referred_by})))
    else:
        user.username=tg_user.username; user.first_name=tg_user.first_name; user.last_name=tg_user.last_name
        user.last_active_at=datetime.now(timezone.utc)
    return user

async def audit(session,actor_id,action,entity=None,entity_id=None,details=None):
    session.add(AuditLog(actor_id=actor_id,action=action,entity=entity,entity_id=entity_id,details=details if isinstance(details,str) else json.dumps(details or {},ensure_ascii=False)))

async def ledger_change(session,user_id:int,amount:Decimal,kind:str,reference:str|None,note:str='') -> Decimal:
    user=await session.get(User,user_id,with_for_update=True)
    if not user: raise ValueError('User not found')
    amount=money(amount); new=money(Decimal(user.balance)+amount)
    if new<0: raise ValueError('Insufficient balance')
    user.balance=new
    session.add(BalanceLedger(user_id=user_id,kind=kind,amount=amount,balance_after=new,reference=reference,note=note))
    return new

async def cashback_change(session,user_id:int,amount:Decimal,kind:str,reference:str|None,note:str='') -> Decimal:
    user=await session.get(User,user_id,with_for_update=True)
    if not user: raise ValueError('User not found')
    amount=money(amount); new=money(Decimal(user.cashback)+amount)
    if new<0: raise ValueError('Insufficient cashback')
    user.cashback=new
    session.add(CashbackLedger(user_id=user_id,kind=kind,amount=amount,balance_after=new,reference=reference,note=note))
    return new

async def event(session,order:Order,actor_id:int|None,event_name:str,details=None):
    session.add(OrderEvent(order_id=order.id,actor_id=actor_id,event=event_name,details=details if isinstance(details,str) else json.dumps(details or {},ensure_ascii=False)))

async def promo_discount(session,user_id:int,code:str,base_amount:Decimal):
    code=(code or '').strip().upper(); base_amount=money(base_amount)
    if not code: return Decimal('0'),None
    p=await session.scalar(select(PromoCode).where(PromoCode.code==code,PromoCode.active==True))
    if not p: raise ValueError('Promo-kod topilmadi yoki faol emas')
    now=datetime.now(timezone.utc)
    if p.expires_at and p.expires_at<now: raise ValueError('Promo-kod muddati tugagan')
    if p.max_uses is not None and p.used_count>=p.max_uses: raise ValueError('Promo-kod limiti tugagan')
    used=await session.scalar(select(PromoUse).where(PromoUse.promo_id==p.id,PromoUse.user_id==user_id))
    if used: raise ValueError('Bu promo-koddan allaqachon foydalangansiz')
    if base_amount<money(p.min_order): raise ValueError(f'Minimal order: {money(p.min_order):,.0f} so‘m')
    discount=(base_amount*Decimal(p.value)/Decimal(100)) if p.kind=='PERCENT' else Decimal(p.value)
    discount=min(money(discount),base_amount)
    return discount,p

async def create_order(session:AsyncSession,user_id:int,order_type:str,quantity:int,target_username:str,amount:Decimal,unit_price:Decimal,item_name=None,comment=None,gift_mode=None,promo_code=None,cost_amount=0):
    amount=money(amount); unit_price=money(unit_price); cost_amount=money(cost_amount)
    user=await session.get(User,user_id,with_for_update=True)
    if not user or user.is_blocked or user.account_status not in {'ACTIVE','VIP'}: raise ValueError('User unavailable')
    discount=Decimal('0'); promo=None
    if promo_code: discount,promo=await promo_discount(session,user_id,promo_code,amount)
    final=money(amount-discount)
    if Decimal(user.balance)<final: raise ValueError(f'Insufficient balance. Kerak: {final} so‘m')
    user.balance=money(Decimal(user.balance)-final)
    user.lifetime_spent=money(Decimal(user.lifetime_spent)+final)
    order=Order(public_id=public_id('ORD'),user_id=user_id,order_type=order_type,status='PENDING',quantity=quantity,target_username=target_username,amount=final,unit_price=unit_price,item_name=item_name,comment=comment,gift_mode=gift_mode,discount=discount,cost_amount=cost_amount,net_profit=money(final-cost_amount))
    session.add(order); await session.flush()
    if promo:
        order.promo_code=promo.code; promo.used_count+=1; session.add(PromoUse(promo_id=promo.id,user_id=user_id,order_id=order.id))
    session.add(BalanceLedger(user_id=user_id,kind='ORDER_DEBIT',amount=-final,balance_after=user.balance,reference=order.public_id,note=f'{order_type} purchase'))
    await event(session,order,user_id,'ORDER_CREATED',{'amount':str(final),'discount':str(discount)})
    return order,final,discount

async def complete_order(session:AsyncSession,order:Order,admin_id:int,cashback_percent:Decimal=Decimal('0')):
    if order.status=='COMPLETED': return Decimal(order.cashback_awarded or 0),Decimal(order.net_profit or 0)
    if order.status in {'CANCELLED','REFUNDED'}: raise ValueError('Order cannot be completed')
    order.status='COMPLETED'; order.completed_at=datetime.now(timezone.utc); order.completed_by=admin_id
    cb=money(Decimal(order.amount)*money(cashback_percent)/Decimal(100)) if cashback_percent else Decimal('0')
    if cb:
        await cashback_change(session,order.user_id,cb,'ORDER_CASHBACK',order.public_id,'Cashback after completed order')
    order.cashback_awarded=cb
    await event(session,order,admin_id,'ORDER_COMPLETED',{'cashback':str(cb)})
    await audit(session,admin_id,'ORDER_COMPLETED','order',order.public_id,{'cashback':str(cb)})
    return cb,Decimal(order.net_profit or 0)

async def refund_order(session:AsyncSession,order:Order,admin_id:int,reason:str=''):
    if order.status=='REFUNDED': return
    if order.status=='COMPLETED': raise ValueError('Completed order requires explicit reversal policy')
    order.status='REFUNDED'; await ledger_change(session,order.user_id,Decimal(order.amount),'REFUND',order.public_id,reason); await event(session,order,admin_id,'ORDER_REFUNDED',{'reason':reason}); await audit(session,admin_id,'ORDER_REFUNDED','order',order.public_id,{'reason':reason})

async def daily_stats(session:AsyncSession):
    today=datetime.now(timezone.utc).date()
    revenue=await session.scalar(select(func.coalesce(func.sum(Payment.amount),0)).where(Payment.status=='APPROVED',func.date(Payment.created_at)==today)) or 0
    orders=await session.scalar(select(func.count(Order.id)).where(Order.status=='COMPLETED',func.date(Order.completed_at)==today)) or 0
    gross=await session.scalar(select(func.coalesce(func.sum(Order.amount),0)).where(Order.status=='COMPLETED',func.date(Order.completed_at)==today)) or 0
    profit=await session.scalar(select(func.coalesce(func.sum(Order.net_profit),0)).where(Order.status=='COMPLETED',func.date(Order.completed_at)==today)) or 0
    pending_pay=await session.scalar(select(func.count(Payment.id)).where(Payment.status=='PENDING')) or 0
    pending_orders=await session.scalar(select(func.count(Order.id)).where(Order.status.in_(['PENDING','PROCESSING']))) or 0
    users=await session.scalar(select(func.count(User.id))) or 0
    return {'revenue':money(revenue),'orders':int(orders),'gross':money(gross),'profit':money(profit),'pending_pay':int(pending_pay),'pending_orders':int(pending_orders),'users':int(users)}
