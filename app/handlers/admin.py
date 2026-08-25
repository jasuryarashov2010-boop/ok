from __future__ import annotations
from datetime import datetime,timezone,timedelta
from decimal import Decimal
import json,asyncio
from aiogram import Router,F,Bot
from aiogram.filters import Command
from aiogram.types import Message,CallbackQuery,BufferedInputFile
from sqlalchemy import select,desc,func,or_
from app.config import settings
from app.db import Session,User,Order,Payment,Gift,StarPackage,Contest,ContestParticipant,PromoCode,SupportTicket,TicketMessage,AuditLog,CostLedger,SystemError,PriceHistory,Broadcast,Notification
from app.services.core import ledger_change,cashback_change,complete_order,refund_order,audit,daily_stats,public_id,event
from app.services.settings_service import get_decimal,get_value,set_value
from app.services.telegram_service import send_admin_log,public_feed
from app.services.redis_service import redis,state_set,state_get,state_del
from app.services.receipt_service import make_receipt
from app.utils.ui import kb,back
from app.utils.text import som
router=Router()

def admin_only(uid:int)->bool:return uid in settings.admins

def admin_kb():
    return kb([
        [('🏠 Dashboard','adash'),('📦 Buyurtmalar','a_orders'),('💳 To‘lovlar','a_payments')],
        [('🚨 Fraud Center','a_fraud'),('🚨 Diqqat markazi','a_attention'),('👥 CRM','a_crm')],
        [('⭐️ Stars','a_stars'),('🎁 Gifts','a_gifts'),('🏆 Konkurs','a_contests')],
        [('🎟 Promo','a_promo'),('🎁 Cashback','a_cashback'),('👥 Referral','a_referral')],
        [('💰 Moliya','a_finance'),('📊 Analitika','a_analytics'),('📜 Audit Log','a_audit')],
        [('👨‍💼 Admin Performance','a_performance'),('📤 Export','a_export')],
        [('💬 Tickets','a_tickets'),('📣 Broadcast','a_broadcast'),('📨 Userga ID orqali habar','a_msgid')],
        [('⚙️ Xizmatlar','a_services'),('🩺 System Health','a_health'),('⚠️ Xatolar','a_errors')],
        [('👑 Adminlar','a_admins'),('🛑 Emergency Mode','a_emergency')]
    ])

def admin_guard(c):
    if not admin_only(c.from_user.id):
        asyncio.create_task(c.answer('⛔️ Ruxsat yo‘q',show_alert=True)); return False
    return True

@router.message(Command('admin'))
async def admin_cmd(m:Message):
    if not admin_only(m.from_user.id):return
    await m.answer('<b>🛠 ADMIN PANEL</b>\n\nBarcha biznes operatsiyalarini shu yerdan boshqarasiz.',reply_markup=admin_kb(),parse_mode='HTML')

@router.callback_query(F.data=='admin')
async def admin(c:CallbackQuery):
    if not admin_guard(c):return
    await c.message.edit_text('<b>🛠 ADMIN PANEL</b>\n\nBarcha biznes operatsiyalari shu markazdan boshqariladi.',reply_markup=admin_kb(),parse_mode='HTML'); await c.answer()

@router.callback_query(F.data.in_({'adash','a_attention'}))
async def dashboard(c:CallbackQuery,bot:Bot):
    if not admin_guard(c):return
    async with Session() as s:
        st=await daily_stats(s)
        active_errors=await s.scalar(select(func.count(SystemError.id)).where(SystemError.resolved==False)) or 0
        fraud=await s.scalar(select(func.count(Payment.id)).where(Payment.status=='PENDING',Payment.risk_status=='HIGH')) or 0
    if c.data=='a_attention':
        txt=(f'<b>🚨 DIQQAT MARKAZI</b>\n\n💳 Kutilayotgan to‘lovlar: <b>{st["pending_pay"]}</b>\n📦 Jarayondagi buyurtmalar: <b>{st["pending_orders"]}</b>\n🔴 High-risk payments: <b>{fraud}</b>\n⚠️ System errors: <b>{active_errors}</b>')
        await c.message.edit_text(txt,reply_markup=kb([[('💳 To‘lovlar','a_payments'),('📦 Orderlar','a_orders')],[('🚨 Fraud','a_fraud'),('⚠️ Xatolar','a_errors')],[('⬅️ Admin','admin')]]),parse_mode='HTML')
    else:
        txt=(f'<b>🏠 ADMIN DASHBOARD</b>\n\n👥 Jami userlar: <b>{st["users"]}</b>\n📦 Bugun bajarilgan: <b>{st["orders"]}</b>\n💳 Bugungi tushum: <b>{som(st["revenue"])}</b>\n💰 Bugungi order tushumi: <b>{som(st["gross"])}</b>\n📈 Bugungi net foyda: <b>{som(st["profit"])}</b>\n\n🔔 Kutilayotgan payment: <b>{st["pending_pay"]}</b>\n📦 Kutilayotgan order: <b>{st["pending_orders"]}</b>\n🚨 High-risk: <b>{fraud}</b>\n⚠️ Errors: <b>{active_errors}</b>')
        await c.message.edit_text(txt,reply_markup=admin_kb(),parse_mode='HTML')
    await c.answer()

@router.callback_query(F.data=='a_orders')
async def orders(c:CallbackQuery):
    if not admin_guard(c):return
    async with Session() as s: os=(await s.execute(select(Order).order_by(desc(Order.id)).limit(30))).scalars().all()
    rows=[]
    for o in os: rows.append([(f'📦 {o.public_id} · {o.order_type} · {som(o.amount)} · {o.status}',f'ord:{o.id}')])
    rows.append([('⬅️ Admin','admin')])
    await c.message.edit_text('<b>📦 BUYURTMALAR</b>\n\nEng so‘nggi buyurtmalar:',reply_markup=kb(rows),parse_mode='HTML')

@router.callback_query(F.data.startswith('ord:'))
async def order_detail(c:CallbackQuery,bot:Bot):
    if not admin_guard(c):return
    oid=int(c.data.split(':')[1])
    async with Session() as s:
        o=await s.get(Order,oid); u=await s.get(User,o.user_id) if o else None
        ev=(await s.execute(select(__import__('app.db',fromlist=['OrderEvent']).OrderEvent).where(__import__('app.db',fromlist=['OrderEvent']).OrderEvent.order_id==oid).order_by(desc(__import__('app.db',fromlist=['OrderEvent']).OrderEvent.id)).limit(15))).scalars().all() if o else []
    if not o:return await c.answer('Order topilmadi',show_alert=True)
    txt=(f'<b>📦 ORDER {o.public_id}</b>\n\n👤 User: <code>{o.user_id}</code> @{u.username if u and u.username else "—"}\n⭐️/Xizmat: <b>{o.item_name or o.order_type}</b>\n🔢 Miqdor: <b>{o.quantity}</b>\n🎯 Target: <b>{o.target_username}</b>\n💰 Summa: <b>{som(o.amount)}</b>\n🏷 Discount: <b>{som(o.discount)}</b>\n🎁 Cashback: <b>{som(o.cashback_awarded)}</b>\n📈 Net profit snapshot: <b>{som(o.net_profit)}</b>\n🕐 Status: <b>{o.status}</b>\n📝 Izoh: {o.comment or "—"}')
    if ev:
        txt+='\n\n<b>📜 Timeline</b>\n'+'\n'.join(f'• {x.created_at:%H:%M:%S} — <b>{x.event}</b>' for x in ev[:10])
    rows=[]
    if o.status=='PENDING':rows.append([('🔄 Jarayonga olish',f'ordproc:{oid}'),('✅ Bajarildi',f'orddone:{oid}')])
    elif o.status=='PROCESSING':rows.append([('✅ Bajarildi',f'orddone:{oid}'),('❌ Bajarilmadi',f'ordfail:{oid}')])
    if o.status in {'PENDING','PROCESSING'}:rows.append([('💰 Refund',f'ordrefund:{oid}')])
    rows.append([('💵 Tannarx','ordcost:'+str(oid)),('📨 Userga habar','msguid:'+str(o.user_id))])
    rows.append([('⬅️ Orderlar','a_orders')])
    await c.message.edit_text(txt,reply_markup=kb(rows),parse_mode='HTML')

@router.callback_query(F.data.startswith('ordproc:'))
async def order_processing(c:CallbackQuery):
    if not admin_guard(c):return
    oid=int(c.data.split(':')[1])
    async with Session() as s:
        o=await s.get(Order,oid)
        if o and o.status=='PENDING':o.status='PROCESSING'; await event(s,o,c.from_user.id,'ORDER_PROCESSING'); await audit(s,c.from_user.id,'ORDER_PROCESSING','order',o.public_id); await s.commit()
    await c.answer('🔄 Jarayonga olindi'); await order_detail(c,None)

@router.callback_query(F.data.startswith('orddone:'))
async def order_done(c:CallbackQuery,bot:Bot):
    if not admin_guard(c):return
    oid=int(c.data.split(':')[1])
    async with Session() as s:
        o=await s.get(Order,oid); u=await s.get(User,o.user_id) if o else None
        cbpct=await get_decimal('cashback_percent',Decimal(settings.cashback_percent))
        if not o:return
        try:cb,profit=await complete_order(s,o,c.from_user.id,cbpct); await s.commit()
        except ValueError as e:await c.answer(str(e),show_alert=True); return
        uid=o.user_id; order_data=o; user_data=u
    try:
        await bot.send_photo(uid,make_receipt(order_data,user_data,settings.bot_name),caption=f'<b>✅ Buyurtma bajarildi!</b>\n\n🧾 <code>{order_data.public_id}</code>\n🎁 Cashback: <b>{som(order_data.cashback_awarded)}</b>',parse_mode='HTML')
    except Exception: pass
    await public_feed(bot,f'🎉 <b>BUYURTMA BAJARILDI</b>\n\n🧾 <code>{order_data.public_id}</code>\n⭐️/Gift: <b>{order_data.quantity} {order_data.order_type}</b>\n➡️ {order_data.target_username}\n✅ Ishonchli xizmat!')
    await send_admin_log(bot,f'✅ ORDER COMPLETED\n{order_data.public_id}\nAdmin: {c.from_user.id}\nCashback: {som(order_data.cashback_awarded)}')
    await c.answer('✅ Bajarildi'); await order_detail(c,bot)

@router.callback_query(F.data.startswith('ordfail:'))
async def order_fail(c:CallbackQuery,bot:Bot):
    if not admin_guard(c):return
    oid=int(c.data.split(':')[1])
    async with Session() as s:
        o=await s.get(Order,oid)
        if not o:return
        try:await refund_order(s,o,c.from_user.id,'Admin marked order failed'); await s.commit()
        except ValueError as e:await c.answer(str(e),show_alert=True);return
        uid=o.user_id; amount=o.amount; public=o.public_id
    try:await bot.send_message(uid,f'<b>❌ Buyurtma bajarilmadi</b>\n\n🧾 <code>{public}</code>\n💰 <b>{som(amount)}</b> balansingizga qaytarildi.',parse_mode='HTML')
    except Exception:pass
    await c.answer('Refund qilindi');await order_detail(c,bot)

@router.callback_query(F.data.startswith('ordrefund:'))
async def order_refund(c:CallbackQuery,bot:Bot):return await order_fail(c,bot) if admin_guard(c) else None

@router.callback_query(F.data.startswith('a_payments'))
async def payments(c:CallbackQuery):
    if not admin_guard(c):return
    async with Session() as s: ps=(await s.execute(select(Payment).order_by(desc(Payment.id)).limit(30))).scalars().all()
    rows=[[(f'💳 {p.public_id} · {som(p.amount)} · {p.status} · {p.risk_status}',f'pay:{p.id}')] for p in ps]
    rows.append([('⬅️ Admin','admin')])
    await c.message.edit_text('<b>💳 TO‘LOVLAR</b>\n\nPending, approved va rejected to‘lovlar:',reply_markup=kb(rows),parse_mode='HTML')

@router.callback_query(F.data.startswith('pay:'))
async def pay_detail(c:CallbackQuery,bot:Bot):
    if not admin_guard(c):return
    pid=int(c.data.split(':')[1])
    async with Session() as s:p=await s.get(Payment,pid)
    if not p:return await c.answer('Topilmadi',show_alert=True)
    txt=f'<b>💳 PAYMENT {p.public_id}</b>\n\n👤 User: <code>{p.user_id}</code>\n💰 Summa: <b>{som(p.amount)}</b>\n🟡 Status: <b>{p.status}</b>\n🚨 Risk: <b>{p.risk_status} ({p.risk_score})</b>\n🔎 OCR summa: <b>{som(p.ocr_amount) if p.ocr_amount else "—"}</b>'
    rows=[]
    if p.status=='PENDING':rows.append([('✅ Tasdiqlash',f'payok:{pid}'),('❌ Rad etish',f'payno:{pid}')])
    rows.append([('📨 Userga ID orqali habar',f'msguid:{p.user_id}')],[('⬅️ To‘lovlar','a_payments')])
    await c.message.edit_text(txt,reply_markup=kb(rows),parse_mode='HTML')
    if p.receipt_file_id:
        try:await bot.send_photo(c.from_user.id,p.receipt_file_id,caption=f'<b>{p.public_id}</b>\nRisk: {p.risk_status}',parse_mode='HTML')
        except Exception:pass

@router.callback_query(F.data.startswith('payok:'))
async def payok(c:CallbackQuery,bot:Bot):
    if not admin_guard(c):return
    pid=int(c.data.split(':')[1])
    async with Session() as s:
        p=await s.get(Payment,pid)
        if not p or p.status!='PENDING':return await c.answer('Allaqachon ko‘rilgan',show_alert=True)
        p.status='APPROVED';p.approved_at=datetime.now(timezone.utc);p.approved_by=c.from_user.id
        bal=await ledger_change(s,p.user_id,p.amount,'TOPUP',p.public_id,'Admin approved payment');await audit(s,c.from_user.id,'PAYMENT_APPROVED','payment',p.public_id,{'balance_after':str(bal)});await s.commit();uid=p.user_id;amt=p.amount
    try:await bot.send_message(uid,f'<b>✅ To‘lovingiz tasdiqlandi!</b>\n\n💰 +<b>{som(amt)}</b>\n💳 Yangi balans: <b>{som(bal)}</b>',parse_mode='HTML')
    except Exception:pass
    await c.answer('✅ Tasdiqlandi');await payments(c)

@router.callback_query(F.data.startswith('payno:'))
async def payno(c:CallbackQuery,bot:Bot):
    if not admin_guard(c):return
    pid=int(c.data.split(':')[1])
    async with Session() as s:
        p=await s.get(Payment,pid)
        if not p:return
        p.status='REJECTED';p.approved_by=c.from_user.id;await audit(s,c.from_user.id,'PAYMENT_REJECTED','payment',p.public_id);await s.commit();uid=p.user_id;public=p.public_id
    try:await bot.send_message(uid,f'❌ <b>{public}</b> to‘lovingiz rad etildi. Supportga murojaat qilishingiz mumkin.',parse_mode='HTML')
    except Exception:pass
    await c.answer('❌ Rad etildi');await payments(c)

@router.callback_query(F.data=='a_stars')
async def stars_admin(c:CallbackQuery):
    if not admin_guard(c):return
    unit=await get_decimal('stars_unit_price',Decimal(settings.stars_unit_price))
    async with Session() as s:packs=(await s.execute(select(StarPackage).order_by(StarPackage.stars))).scalars().all()
    txt=f'<b>⭐️ STARS BOSHQARUVI</b>\n\n1 Stars: <b>{som(unit)}</b>\n\nPaketlar:\n'+'\n'.join(f'• {p.stars} — {"ON" if p.active else "OFF"}' for p in packs)
    await c.message.edit_text(txt,reply_markup=kb([[('💰 Narxni o‘zgartirish','astarprice'),('➕ Paket','astarpack')],[('⬅️ Admin','admin')]]),parse_mode='HTML')
@router.callback_query(F.data=='astarprice')
async def astarprice(c:CallbackQuery):
    if not admin_guard(c):return
    await redis.set(f'fsm:{c.from_user.id}','a_price',ex=600);await c.message.edit_text('<b>💰 1 Stars narxini yuboring</b>',reply_markup=back('a_stars'),parse_mode='HTML')
@router.callback_query(F.data=='astarpack')
async def astarpack(c:CallbackQuery):
    if not admin_guard(c):return
    await redis.set(f'fsm:{c.from_user.id}','a_pack',ex=600);await c.message.edit_text('<b>➕ Stars paket</b>\n\nMasalan: <code>250</code>',reply_markup=back('a_stars'),parse_mode='HTML')

@router.callback_query(F.data=='a_gifts')
async def gifts_admin(c:CallbackQuery):
    if not admin_guard(c):return
    async with Session() as s:gs=(await s.execute(select(Gift).order_by(Gift.id))).scalars().all()
    unit=await get_decimal('stars_unit_price',Decimal(settings.stars_unit_price));rows=[]
    for g in gs:rows.append([(f'🎁 {g.name} · {g.stars} · {som(g.stars*unit)} · {"ON" if g.active else "OFF"}',f'gt:{g.id}')])
    rows.append([('➕ Gift qo‘shish','giftadd'),('⬅️ Admin','admin')])
    await c.message.edit_text('<b>🎁 GIFT BOSHQARUVI</b>\n\nDinamik narx Stars narxidan hisoblanadi.',reply_markup=kb(rows),parse_mode='HTML')
@router.callback_query(F.data.startswith('gt:'))
async def gift_toggle(c:CallbackQuery):
    if not admin_guard(c):return
    gid=int(c.data.split(':')[1]);
    async with Session() as s:g=await s.get(Gift,gid);g.active=not g.active;await audit(s,c.from_user.id,'GIFT_TOGGLED','gift',str(g.id),{'active':g.active});await s.commit()
    await c.answer('Holat o‘zgardi');await gifts_admin(c)
@router.callback_query(F.data=='giftadd')
async def giftadd(c:CallbackQuery):
    if not admin_guard(c):return
    await redis.set(f'fsm:{c.from_user.id}','a_giftadd',ex=900);await c.message.edit_text('<b>➕ Gift qo‘shish</b>\n\nFormat: <code>telegram_gift_id|Nomi|Stars</code>',reply_markup=back('a_gifts'),parse_mode='HTML')

@router.callback_query(F.data=='a_contests')
async def contests(c:CallbackQuery):
    if not admin_guard(c):return
    async with Session() as s:cs=(await s.execute(select(Contest).order_by(desc(Contest.id)).limit(20))).scalars().all()
    rows=[[('➕ Konkurs qo‘shish','contestadd')]]
    for x in cs:rows.append([(f'🏆 {x.title} · {"ON" if x.active else "OFF"} · {"DONE" if x.finished else "LIVE"}',f'ct:{x.id}')])
    rows.append([('⬅️ Admin','admin')]);await c.message.edit_text('<b>🏆 KONKURSLAR</b>',reply_markup=kb(rows),parse_mode='HTML')
@router.callback_query(F.data=='contestadd')
async def contestadd(c:CallbackQuery):
    if not admin_guard(c):return
    await redis.set(f'fsm:{c.from_user.id}','a_contest',ex=900);await c.message.edit_text('<b>🏆 Konkurs qo‘shish</b>\n\nFormat:\n<code>nom|tavsif|sovrin|soat|g‘oliblar</code>',reply_markup=back('a_contests'),parse_mode='HTML')
@router.callback_query(F.data.startswith('ct:'))
async def contest_toggle(c:CallbackQuery):
    if not admin_guard(c):return
    cid=int(c.data.split(':')[1]);
    async with Session() as s:x=await s.get(Contest,cid);x.active=not x.active;await audit(s,c.from_user.id,'CONTEST_TOGGLED','contest',str(cid),{'active':x.active});await s.commit()
    await contests(c);await c.answer('✅ Holat o‘zgardi')

@router.callback_query(F.data=='a_crm')
async def crm(c:CallbackQuery):
    if not admin_guard(c):return
    await redis.set(f'fsm:{c.from_user.id}','a_crm',ex=600);await c.message.edit_text('<b>👥 CRM</b>\n\nTelegram ID yoki @username yuboring.',reply_markup=back('admin'),parse_mode='HTML')

@router.callback_query(F.data=='a_msgid')
async def msgid(c:CallbackQuery):
    if not admin_guard(c):return
    await redis.set(f'fsm:{c.from_user.id}','a_msgid',ex=900);await c.message.edit_text('<b>📨 Userga ID orqali habar</b>\n\nFoydalanuvchining Telegram ID raqamini yuboring.',reply_markup=back('admin'),parse_mode='HTML')
@router.callback_query(F.data.startswith('msguid:'))
async def msguid(c:CallbackQuery):
    if not admin_guard(c):return
    uid=int(c.data.split(':')[1]);await redis.set(f'fsm:{c.from_user.id}',f'a_msgtext:{uid}',ex=900);await c.message.edit_text(f'<b>📨 User ID: <code>{uid}</code></b>\n\nYuboriladigan HTML xabarni yozing.',reply_markup=back('a_payments'),parse_mode='HTML')

@router.callback_query(F.data=='a_finance')
async def finance(c:CallbackQuery):
    if not admin_guard(c):return
    async with Session() as s:st=await daily_stats(s)
    await c.message.edit_text(f'<b>💰 MOLIYA</b>\n\n💳 Tushum: <b>{som(st["revenue"])}</b>\n📦 Order gross: <b>{som(st["gross"])}</b>\n📈 Net profit: <b>{som(st["profit"])}</b>\n📦 Completed: <b>{st["orders"]}</b>\n\nFormula: Revenue − cost − cashback − discount − refund.',reply_markup=back('admin'),parse_mode='HTML')
@router.callback_query(F.data=='a_analytics')
async def analytics(c:CallbackQuery):
    if not admin_guard(c):return
    async with Session() as s:
        users=await s.scalar(select(func.count(User.id))) or 0;buyers=await s.scalar(select(func.count(func.distinct(Order.user_id))).where(Order.status=='COMPLETED')) or 0
        avg=await s.scalar(select(func.avg(Order.amount)).where(Order.status=='COMPLETED')) or 0
        top=(await s.execute(select(Order.item_name,func.count(Order.id)).where(Order.status=='COMPLETED').group_by(Order.item_name).order_by(desc(func.count(Order.id))).limit(5))).all()
    txt=f'<b>📊 ANALITIKA</b>\n\n👥 Users: <b>{users}</b>\n🛒 Buyers: <b>{buyers}</b>\n🎯 Conversion: <b>{(buyers/users*100):.2f}%</b>\n🧮 Avg order: <b>{som(avg)}</b>\n\n<b>Top services</b>\n'+'\n'.join(f'• {n or "—"}: {c}' for n,c in top)
    await c.message.edit_text(txt,reply_markup=back('admin'),parse_mode='HTML')
@router.callback_query(F.data=='a_audit')
async def audit_logs(c:CallbackQuery):
    if not admin_guard(c):return
    async with Session() as s:xs=(await s.execute(select(AuditLog).order_by(desc(AuditLog.id)).limit(30))).scalars().all()
    txt='<b>📜 AUDIT LOG</b>\n\n'+'\n'.join(f'• {x.created_at:%d.%m %H:%M} · {x.action} · {x.entity or ""} · {x.actor_id or "system"}' for x in xs)
    await c.message.edit_text(txt,reply_markup=back('admin'),parse_mode='HTML')
@router.callback_query(F.data=='a_tickets')
async def tickets(c:CallbackQuery):
    if not admin_guard(c):return
    async with Session() as s:ts=(await s.execute(select(SupportTicket).order_by(desc(SupportTicket.id)).limit(30))).scalars().all()
    rows=[[(f'💬 {t.public_id} · {t.status} · user {t.user_id}',f'ticket:{t.id}')] for t in ts];rows.append([('⬅️ Admin','admin')])
    await c.message.edit_text('<b>💬 TICKETS</b>',reply_markup=kb(rows),parse_mode='HTML')
@router.callback_query(F.data.startswith('ticket:'))
async def ticket_detail(c:CallbackQuery):
    if not admin_guard(c):return
    tid=int(c.data.split(':')[1]);
    async with Session() as s:t=await s.get(SupportTicket,tid)
    await c.message.edit_text(f'<b>💬 {t.public_id}</b>\n\n👤 User: <code>{t.user_id}</code>\n📌 {t.status}\n\n{t.message}',reply_markup=kb([[('📨 Javob berish',f'msguid:{t.user_id}'),('✅ Yopish',f'tclose:{tid}')],[('⬅️ Tickets','a_tickets')]]),parse_mode='HTML')
@router.callback_query(F.data.startswith('tclose:'))
async def ticket_close(c:CallbackQuery):
    if not admin_guard(c):return
    tid=int(c.data.split(':')[1]);
    async with Session() as s:t=await s.get(SupportTicket,tid);t.status='CLOSED';t.closed_at=datetime.now(timezone.utc);await audit(s,c.from_user.id,'TICKET_CLOSED','ticket',t.public_id);await s.commit()
    await tickets(c)

@router.callback_query(F.data=='a_broadcast')
async def broadcast(c:CallbackQuery):
    if not admin_guard(c):return
    await redis.set(f'fsm:{c.from_user.id}','a_broadcast',ex=900);await c.message.edit_text('<b>📣 BROADCAST</b>\n\nAvval format yuboring:\n<code>SEGMENT|HTML MATN</code>\n\nSegment: ALL, NEW, BUYERS, VIP',reply_markup=back('admin'),parse_mode='HTML')

@router.callback_query(F.data=='a_promo')
async def promo(c:CallbackQuery):
    if not admin_guard(c):return
    await redis.set(f'fsm:{c.from_user.id}','a_promo',ex=900);await c.message.edit_text('<b>🎟 PROMO-KOD</b>\n\nFormat:\n<code>KOD|FIXED|5000|100|0</code>\n yoki <code>KOD|PERCENT|10|100|50000</code>\n\nmax uses va minimal order.',reply_markup=back('admin'),parse_mode='HTML')

@router.callback_query(F.data=='a_cashback')
async def cashback_admin(c:CallbackQuery):
    if not admin_guard(c):return
    cb=await get_decimal('cashback_percent',Decimal(settings.cashback_percent));await redis.set(f'fsm:{c.from_user.id}','a_cashback',ex=600)
    await c.message.edit_text(f'<b>🎁 CASHBACK</b>\n\nAmaldagi: <b>{cb}%</b>\n\nYangi foizni yuboring.',reply_markup=back('admin'),parse_mode='HTML')
@router.callback_query(F.data=='a_referral')
async def referral_admin(c:CallbackQuery):
    if not admin_guard(c):return
    rb=await get_decimal('referral_bonus',Decimal(settings.referral_bonus));await redis.set(f'fsm:{c.from_user.id}','a_referral',ex=600)
    await c.message.edit_text(f'<b>👥 REFERRAL</b>\n\nBonus: <b>{som(rb)}</b>\n\nYangi bonusni yuboring.',reply_markup=back('admin'),parse_mode='HTML')

@router.callback_query(F.data=='a_services')
async def services(c:CallbackQuery):
    if not admin_guard(c):return
    keys=['service_stars','service_gifts','service_topup','service_contest','service_promo']
    lines=['<b>⚙️ XIZMATLAR</b>',''];rows=[]
    for k in keys:
        val=str(await get_value(k,'true')).lower() in {'true','1','on','yes'}; name=k.replace('service_','').upper();lines.append(f'{name}: <b>{"🟢 ON" if val else "🔴 OFF"}</b>');rows.append([(f'{"🟢" if val else "🔴"} {name}',f'toggle:{k}')])
    rows.append([('⬅️ Admin','admin')]);await c.message.edit_text('\n'.join(lines),reply_markup=kb(rows),parse_mode='HTML')
@router.callback_query(F.data.startswith('toggle:'))
async def toggle(c:CallbackQuery):
    if not admin_guard(c):return
    k=c.data.split(':',1)[1];cur=str(await get_value(k,'true')).lower() in {'true','1','on','yes'};await set_value(k,'false' if cur else 'true');await audit_value(c.from_user.id,k,not cur);await services(c)
async def audit_value(admin_id,key,value):
    async with Session() as s:await audit(s,admin_id,'SERVICE_TOGGLED','setting',key,{'value':value});await s.commit()

@router.callback_query(F.data=='a_health')
async def health(c:CallbackQuery):
    if not admin_guard(c):return
    db='🟢';rd='🟢'
    try:
        async with Session() as s:await s.scalar(select(1))
    except:db='🔴'
    try:await redis.ping()
    except:rd='🔴'
    await c.message.edit_text(f'<b>🩺 SYSTEM HEALTH</b>\n\nPostgreSQL: {db}\nRedis: {rd}\nWorker: 🟢 (service-level)\nTelegram: 🟢 (worker running)',reply_markup=back('admin'),parse_mode='HTML')
@router.callback_query(F.data=='a_errors')
async def errors(c:CallbackQuery):
    if not admin_guard(c):return
    async with Session() as s:xs=(await s.execute(select(SystemError).where(SystemError.resolved==False).order_by(desc(SystemError.id)).limit(20))).scalars().all()
    txt='<b>⚠️ XATOLAR</b>\n\n'+'\n'.join(f'• <code>{x.public_id}</code> · {x.module}\n{x.message[:140]}' for x in xs) if xs else '<b>⚠️ XATOLAR</b>\n\n✅ Faol xatolar yo‘q.'
    await c.message.edit_text(txt,reply_markup=back('admin'),parse_mode='HTML')
@router.callback_query(F.data=='a_fraud')
async def fraud_center(c:CallbackQuery):
    if not admin_guard(c):return
    async with Session() as s:ps=(await s.execute(select(Payment).where(Payment.status=='PENDING',Payment.risk_status.in_(['HIGH','MEDIUM'])).order_by(desc(Payment.id)).limit(20))).scalars().all()
    rows=[[(f'🚨 {p.public_id} · {p.risk_status} {p.risk_score} · {som(p.amount)}',f'pay:{p.id}')] for p in ps];rows.append([('⬅️ Admin','admin')])
    await c.message.edit_text('<b>🚨 FRAUD CENTER</b>\n\nHigh/Medium risk pending payments:',reply_markup=kb(rows),parse_mode='HTML')
@router.callback_query(F.data=='a_admins')
async def admins(c:CallbackQuery):
    if not admin_guard(c):return
    await c.message.edit_text('<b>👑 ADMİNLAR</b>\n\n'+'\n'.join(f'• <code>{x}</code>' for x in sorted(settings.admins))+'\n\nRollarni productionda RBAC jadvali orqali kengaytirish mumkin.',reply_markup=back('admin'),parse_mode='HTML')
@router.callback_query(F.data=='a_emergency')
async def emergency(c:CallbackQuery):
    if not admin_guard(c):return
    cur=str(await get_value('emergency_mode','false')).lower()=='true';await set_value('emergency_mode','false' if cur else 'true');await audit_value(c.from_user.id,'emergency_mode',not cur)
    await c.message.edit_text(f'<b>🛑 EMERGENCY MODE</b>\n\nHozir: <b>{"🟢 OFF" if cur else "🔴 ON"}</b>\n\nON bo‘lsa yangi moliyaviy orderlar vaqtincha bloklanadi.',reply_markup=back('admin'),parse_mode='HTML')


@router.callback_query(F.data.startswith('ordcost:'))
async def order_cost(c:CallbackQuery):
    if not admin_guard(c):return
    oid=int(c.data.split(':')[1]);await redis.set(f'fsm:{c.from_user.id}',f'a_cost:{oid}',ex=600)
    await c.message.edit_text('<b>💵 Buyurtma tannarxi</b>\n\nTannarxni so‘mda yuboring. Bu net foyda hisobida ishlatiladi.',reply_markup=back(f'ord:{oid}'),parse_mode='HTML')

@router.callback_query(F.data=='a_performance')
async def performance(c:CallbackQuery):
    if not admin_guard(c):return
    async with Session() as s:
        rows=(await s.execute(select(Order.completed_by,func.count(Order.id),func.coalesce(func.sum(Order.amount),0)).where(Order.status=='COMPLETED').group_by(Order.completed_by).order_by(desc(func.count(Order.id))))).all()
    txt='<b>👨‍💼 ADMIN PERFORMANCE</b>\n\n'+'\n'.join(f'• Admin <code>{aid or "—"}</code> · {cnt} order · {som(total)}' for aid,cnt,total in rows) if rows else '<b>👨‍💼 ADMIN PERFORMANCE</b>\n\nHali bajarilgan order yo‘q.'
    await c.message.edit_text(txt,reply_markup=back('admin'),parse_mode='HTML')

@router.callback_query(F.data=='a_export')
async def export_center(c:CallbackQuery):
    if not admin_guard(c):return
    await c.message.edit_text('<b>📤 EXPORT</b>\n\nQaysi ma’lumotni olish kerak?',reply_markup=kb([[('📦 Orders','export:orders'),('💳 Payments','export:payments')],[('👥 Users','export:users')],[('⬅️ Admin','admin')]]),parse_mode='HTML')

@router.callback_query(F.data.startswith('export:'))
async def export_data(c:CallbackQuery):
    if not admin_guard(c):return
    import csv,io
    kind=c.data.split(':',1)[1]
    out=io.StringIO();w=csv.writer(out)
    async with Session() as s:
        if kind=='orders':
            rows=(await s.execute(select(Order).order_by(Order.id))).scalars().all();w.writerow(['public_id','user_id','type','status','quantity','target','amount','discount','profit','created_at','completed_at']);[w.writerow([o.public_id,o.user_id,o.order_type,o.status,o.quantity,o.target_username,o.amount,o.discount,o.net_profit,o.created_at,o.completed_at]) for o in rows];name='orders.csv'
        elif kind=='payments':
            rows=(await s.execute(select(Payment).order_by(Payment.id))).scalars().all();w.writerow(['public_id','user_id','amount','status','risk','created_at','approved_at']);[w.writerow([p.public_id,p.user_id,p.amount,p.status,p.risk_status,p.created_at,p.approved_at]) for p in rows];name='payments.csv'
        else:
            rows=(await s.execute(select(User).order_by(User.id))).scalars().all();w.writerow(['id','username','balance','cashback','referrals','lifetime_spent','status','created_at']);[w.writerow([u.id,u.username,u.balance,u.cashback,u.referral_count,u.lifetime_spent,u.account_status,u.created_at]) for u in rows];name='users.csv'
    await c.message.answer_document(BufferedInputFile(out.getvalue().encode('utf-8-sig'),filename=name),caption=f'<b>📤 {name}</b>',parse_mode='HTML');await c.answer('✅ Tayyor')

@router.message()
async def admin_text(m:Message,bot:Bot):
    if not admin_only(m.from_user.id):return
    state=await redis.get(f'fsm:{m.from_user.id}')
    if not state:return
    # direct message has dynamic state
    if state.startswith('a_msgtext:'):
        uid=int(state.split(':')[1])
        try:
            await bot.send_message(uid,m.text,parse_mode='HTML')
            async with Session() as s:await audit(s,m.from_user.id,'DIRECT_USER_MESSAGE','user',str(uid),{'text':m.text[:500]});await s.commit()
            await m.answer(f'✅ Xabar <code>{uid}</code> ID ga yuborildi.',parse_mode='HTML')
        except Exception as e:await m.answer(f'❌ Yuborib bo‘lmadi: <code>{type(e).__name__}</code>',parse_mode='HTML')
        await redis.delete(f'fsm:{m.from_user.id}');return
    async with Session() as s:
        if state.startswith('a_cost:'):
            try:
                oid=int(state.split(':',1)[1]); cost=Decimal(m.text.strip()); o=await s.get(Order,oid); o.cost_amount=cost; o.net_profit=Decimal(o.amount)-cost; s.add(CostLedger(order_id=oid,kind='ORDER_COST',amount=cost,note='Admin entered cost')); await audit(s,m.from_user.id,'ORDER_COST_SET','order',o.public_id,{'cost':str(cost)}); await s.commit(); await m.answer(f'✅ Tannarx saqlandi: <b>{som(cost)}</b>\nNet profit: <b>{som(o.net_profit)}</b>',parse_mode='HTML')
            except Exception as e: await m.answer(f'❌ Tannarx noto‘g‘ri: {type(e).__name__}')
            await redis.delete(f'fsm:{m.from_user.id}'); return
        if state=='a_msgid':
            try:uid=int(m.text.strip());await redis.set(f'fsm:{m.from_user.id}',f'a_msgtext:{uid}',ex=900);await m.answer(f'<b>📨 {uid}</b>\n\nEndi HTML xabarni yuboring.',parse_mode='HTML');return
            except:await m.answer('❌ ID raqam bo‘lishi kerak.');return
        if state=='a_price':
            try:new=Decimal(m.text);old=await get_decimal('stars_unit_price',Decimal(settings.stars_unit_price));await set_value('stars_unit_price',str(new));s.add(PriceHistory(key='stars_unit_price',old_value=str(old),new_value=str(new),admin_id=m.from_user.id));await audit(s,m.from_user.id,'PRICE_CHANGED','setting','stars_unit_price',{'new':str(new)});await s.commit();await m.answer(f'✅ 1 Stars = <b>{som(new)}</b>',parse_mode='HTML')
            except:await m.answer('❌ Narx noto‘g‘ri.')
        elif state=='a_pack':
            try:n=int(m.text);s.add(StarPackage(stars=n,active=True));await s.commit();await m.answer(f'✅ {n} Stars paket qo‘shildi.')
            except:await m.answer('❌ Paket noto‘g‘ri.')
        elif state=='a_giftadd':
            try:tid,name,stars=m.text.split('|',2);s.add(Gift(telegram_gift_id=tid.strip(),name=name.strip(),stars=int(stars),active=True));await s.commit();await m.answer('✅ Gift qo‘shildi.')
            except Exception as e:await m.answer(f'❌ Format: {type(e).__name__}')
        elif state=='a_contest':
            try:title,desc,prize,hours,wc=m.text.split('|',4);now=datetime.now(timezone.utc);s.add(Contest(title=title,description=desc,prize=prize,starts_at=now,ends_at=now+timedelta(hours=int(hours)),winner_count=int(wc),active=True));await s.commit();await m.answer('✅ Konkurs yaratildi.')
            except:await m.answer('❌ Format noto‘g‘ri.')
        elif state=='a_promo':
            try:
                code,kind,val,maxuses,minorder=m.text.split('|',4);s.add(PromoCode(code=code.upper(),kind=kind.upper(),value=Decimal(val),max_uses=int(maxuses),min_order=Decimal(minorder),active=True));await s.commit();await m.answer('✅ Promo-kod yaratildi.')
            except:await m.answer('❌ Format noto‘g‘ri.')
        elif state=='a_cashback':
            try:v=Decimal(m.text);await set_value('cashback_percent',str(v));await audit(s,m.from_user.id,'CASHBACK_CHANGED','setting','cashback_percent',{'new':str(v)});await s.commit();await m.answer(f'✅ Cashback {v}% bo‘ldi.')
            except:await m.answer('❌ Foiz noto‘g‘ri.')
        elif state=='a_referral':
            try:v=Decimal(m.text);await set_value('referral_bonus',str(v));await audit(s,m.from_user.id,'REFERRAL_BONUS_CHANGED','setting','referral_bonus',{'new':str(v)});await s.commit();await m.answer(f'✅ Referral bonus {som(v)} bo‘ldi.')
            except:await m.answer('❌ Summa noto‘g‘ri.')
        elif state=='a_crm':
            q=m.text.strip().lstrip('@')
            try:u=await s.get(User,int(q))
            except:u=await s.scalar(select(User).where(User.username.ilike(q)))
            if not u:await m.answer('❌ User topilmadi.')
            else:await m.answer(f'<b>👥 CRM</b>\n\n🆔 <code>{u.id}</code>\n👤 @{u.username or "—"}\n💰 {som(u.balance)}\n🎁 Cashback: {som(u.cashback)}\n👥 Referrals: {u.referral_count}\n💵 Lifetime: {som(u.lifetime_spent)}\n🚦 {u.account_status}',parse_mode='HTML')
        elif state=='a_broadcast':
            try:segment,text=m.text.split('|',1);segment=segment.upper();b=Broadcast(actor_id=m.from_user.id,text=text,segment=segment,status='QUEUED');s.add(b);await s.flush();await redis.rpush('broadcast:queue',json.dumps({'id':b.id},ensure_ascii=False));await s.commit();await m.answer(f'✅ Broadcast #{b.id} navbatga qo‘yildi.')
            except:await m.answer('❌ Format: SEGMENT|TEXT')
    await redis.delete(f'fsm:{m.from_user.id}')
