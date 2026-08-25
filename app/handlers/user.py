from __future__ import annotations
from datetime import datetime,timezone
from decimal import Decimal
import re,io
from aiogram import Router,F,Bot
from aiogram.filters import CommandStart
from aiogram.types import Message,CallbackQuery,InlineKeyboardMarkup,InlineKeyboardButton
from sqlalchemy import select,desc
from app.db import Session,User,Order,Payment,Gift,StarPackage,Contest,ContestParticipant,PromoCode,PromoUse,Notification,SupportTicket,TicketMessage
from app.config import settings
from app.services.core import get_or_create_user,create_order,ledger_change,audit,public_id,daily_stats,event
from app.services.settings_service import get_decimal,get_value
from app.services.telegram_service import is_subscribed,send_admin_log,public_feed
from app.services.redis_service import rate_limit,state_set,state_get,state_del
from app.services.fraud import sha256_bytes,assess_receipt
from app.utils.ui import main_menu,back,kb,confirm
from app.utils.text import home_text,som

router=Router()

def joined_kb(missing):
    rows=[]
    for channel in missing:
        url=f'https://t.me/{channel.lstrip('@')}' if channel.startswith('@') else None
        if url: rows.append([InlineKeyboardButton(text=f'📢 Kanalga qo‘shilish · {channel}',url=url)])
    rows.append([InlineKeyboardButton(text='✅ Obunani tekshirish',callback_data='checksub')])
    return InlineKeyboardMarkup(inline_keyboard=rows)
async def gate(x:Message|CallbackQuery,bot:Bot):
    ok,missing=await is_subscribed(bot,x.from_user.id)
    if ok:return True
    text='<b>🔒 Majburiy obuna</b>\n\nBotdan foydalanish uchun barcha majburiy kanallarga obuna bo‘ling va keyin tekshiring.'
    if isinstance(x,Message): await x.answer(text,reply_markup=joined_kb(missing),parse_mode='HTML')
    else: await x.message.edit_text(text,reply_markup=joined_kb(missing),parse_mode='HTML')
    return False

def username_ok(v): return bool(re.fullmatch(r'@[A-Za-z0-9_]{4,64}',v.strip()))

@router.message(CommandStart())
async def start(m:Message,bot:Bot):
    if not await rate_limit(f'start:{m.from_user.id}',5,30): return
    ref=None; parts=(m.text or '').split(maxsplit=1)
    if len(parts)==2 and parts[1].startswith('ref_'):
        try: ref=int(parts[1].split('_',1)[1])
        except: pass
    async with Session() as s:
        user=await get_or_create_user(s,m.from_user,ref); await s.commit()
    if not await gate(m,bot): return
    await m.answer(home_text(user,settings.bot_name),reply_markup=main_menu(m.from_user.id in settings.admins),parse_mode='HTML')

@router.callback_query(F.data=='noop')
async def noop(c:CallbackQuery): await c.answer('Telegram kanaliga qo‘shiling.',show_alert=True)
@router.callback_query(F.data=='checksub')
async def checksub(c:CallbackQuery,bot:Bot):
    if not await gate(c,bot): return
    async with Session() as s:u=await s.get(User,c.from_user.id)
    await c.message.edit_text(home_text(u,settings.bot_name),reply_markup=main_menu(c.from_user.id in settings.admins),parse_mode='HTML'); await c.answer('✅ Tasdiqlandi')
@router.callback_query(F.data=='home')
async def home(c:CallbackQuery,bot:Bot):
    if not await gate(c,bot):return
    async with Session() as s:u=await s.get(User,c.from_user.id)
    await c.message.edit_text(home_text(u,settings.bot_name),reply_markup=main_menu(c.from_user.id in settings.admins),parse_mode='HTML')

@router.callback_query(F.data=='profile')
async def profile(c:CallbackQuery,bot:Bot):
    if not await gate(c,bot):return
    async with Session() as s:u=await s.get(User,c.from_user.id)
    txt=(f'<b>👤 Profilim</b>\n\n🆔 ID: <code>{u.id}</code>\n👤 @{u.username or "—"}\n💰 Balans: <b>{som(u.balance)}</b>\n🎁 Cashback: <b>{som(u.cashback)}</b>\n👥 Referral: <b>{u.referral_count}</b>\n💵 Lifetime: <b>{som(u.lifetime_spent)}</b>\n📌 Status: <b>{u.account_status}</b>')
    await c.message.edit_text(txt,reply_markup=kb([[('💰 Hisobim','account'),('📦 Buyurtmalarim','myorders')],[('👥 Referral','referral'),('🎁 Cashback','cashback')],[('⬅️ Orqaga','home')]]),parse_mode='HTML')
@router.callback_query(F.data=='account')
async def account(c:CallbackQuery,bot:Bot):
    if not await gate(c,bot):return
    async with Session() as s:u=await s.get(User,c.from_user.id)
    await c.message.edit_text(f'<b>💰 Hisobim</b>\n\nAsosiy balans: <b>{som(u.balance)}</b>\n🎁 Cashback: <b>{som(u.cashback)}</b>',reply_markup=back('profile'),parse_mode='HTML')
@router.callback_query(F.data=='cashback')
async def cashback(c:CallbackQuery,bot:Bot):
    if not await gate(c,bot):return
    async with Session() as s:u=await s.get(User,c.from_user.id)
    await c.message.edit_text(f'<b>🎁 Cashback</b>\n\nMavjud cashback: <b>{som(u.cashback)}</b>\n\nCashback har bir muvaffaqiyatli buyurtmadan keyin hisoblanadi.',reply_markup=back('profile'),parse_mode='HTML')
@router.callback_query(F.data=='myorders')
async def myorders(c:CallbackQuery,bot:Bot):
    if not await gate(c,bot):return
    async with Session() as s: orders=(await s.execute(select(Order).where(Order.user_id==c.from_user.id).order_by(desc(Order.id)).limit(25))).scalars().all()
    lines=['<b>📦 Buyurtmalarim</b>','']
    for o in orders: lines.append(f'🧾 <code>{o.public_id}</code> · {o.order_type} · {o.quantity} · {som(o.amount)} · <b>{o.status}</b>')
    if len(lines)==2:lines.append('Hozircha buyurtma yo‘q.')
    await c.message.edit_text('\n'.join(lines),reply_markup=back('profile'),parse_mode='HTML')
@router.callback_query(F.data=='referral')
async def referral(c:CallbackQuery,bot:Bot):
    if not await gate(c,bot):return
    link=f'https://t.me/{settings.bot_username}?start=ref_{c.from_user.id}' if settings.bot_username else 'Bot username sozlanmagan'
    async with Session() as s:u=await s.get(User,c.from_user.id)
    await c.message.edit_text(f'<b>👥 Referral</b>\n\nTaklif qilinganlar: <b>{u.referral_count}</b>\n\n🔗 <code>{link}</code>',reply_markup=back('profile'),parse_mode='HTML')

@router.callback_query(F.data=='stars')
async def stars(c:CallbackQuery,bot:Bot):
    if not await gate(c,bot):return
    async with Session() as s:
        packs=(await s.execute(select(StarPackage).where(StarPackage.active==True).order_by(StarPackage.stars))).scalars().all()
        if not packs:
            for x in [50,75,100,200,500,1000]:s.add(StarPackage(stars=x,active=True))
            await s.commit(); packs=(await s.execute(select(StarPackage).where(StarPackage.active==True).order_by(StarPackage.stars))).scalars().all()
    unit=await get_decimal('stars_unit_price',Decimal(settings.stars_unit_price))
    rows=[[(f'⭐️ {p.stars} Stars · {som(p.stars*unit)}',f'starspick:{p.stars}')] for p in packs]
    rows.append([('✏️ Boshqa miqdor','stars_custom'),('⬅️ Orqaga','home')])
    txt=f'<b>⭐️ Qancha Stars olasiz?</b>\n\n⚡️ Tezkor navbat\n✅ Ishonchli xizmat\n\nMin: <b>50 Stars</b>\nMax: <b>Cheksiz</b>\n\n<b>Kerakli miqdorni tanlang:</b>\n\n⭐️ 1 Stars = <b>{som(unit)}</b>'
    await c.message.edit_text(txt,reply_markup=kb(rows),parse_mode='HTML')
@router.callback_query(F.data=='stars_custom')
async def stars_custom(c:CallbackQuery,bot:Bot):
    if not await gate(c,bot):return
    await state_set(c.from_user.id,'stars_amount',{},600); await c.message.edit_text('<b>✏️ Stars miqdorini yuboring</b>\n\nMin: <b>50 Stars</b>',reply_markup=back('stars'),parse_mode='HTML')
@router.callback_query(F.data.startswith('starspick:'))
async def starspick(c:CallbackQuery,bot:Bot):
    if not await gate(c,bot):return
    stars=int(c.data.split(':')[1]); await state_set(c.from_user.id,'stars_target',{'stars':stars},600)
    await c.message.edit_text(f'<b>⭐️ {stars} Stars</b>\n\nStars yuboriladigan profilni <code>@username</code> ko‘rinishida yuboring:',reply_markup=back('stars'),parse_mode='HTML')

@router.callback_query(F.data=='gifts')
async def gifts(c:CallbackQuery,bot:Bot):
    if not await gate(c,bot):return
    async with Session() as s: gs=(await s.execute(select(Gift).where(Gift.active==True).order_by(Gift.stars,Gift.id))).scalars().all()
    unit=await get_decimal('stars_unit_price',Decimal(settings.stars_unit_price))
    rows=[]
    for g in gs:
        avail=f' · {g.available} dona' if g.available is not None else ''
        rows.append([(f'🎁 {g.name} · {g.stars} Stars · {som(g.stars*unit)}{avail}',f'giftpick:{g.id}')])
    rows.append([('⬅️ Orqaga','home')])
    await c.message.edit_text('<b>🎁 Qanday Gift olmoqchisiz?</b>\n\nKerakli giftni tanlang:',reply_markup=kb(rows),parse_mode='HTML')
@router.callback_query(F.data.startswith('giftpick:'))
async def giftpick(c:CallbackQuery,bot:Bot):
    if not await gate(c,bot):return
    gid=int(c.data.split(':')[1]); async_=None
    async with Session() as s:g=await s.get(Gift,gid)
    if not g or not g.active: await c.answer('Gift mavjud emas.',show_alert=True); return
    await state_set(c.from_user.id,'gift_target',{'gift_id':gid},900)
    await c.message.edit_text(f'<b>🎯 Kimga yuboriladi?</b>\n\nGift: <b>{g.name}</b>\n\nProfil username\'ini <code>@username</code> ko‘rinishida yuboring.',reply_markup=back('gifts'),parse_mode='HTML')

@router.callback_query(F.data.startswith('giftmode:'))
async def giftmode(c:CallbackQuery,bot:Bot):
    if not await gate(c,bot):return
    data=await state_get(c.from_user.id,'gift_target')
    if not data:return
    data['mode']=c.data.split(':',1)[1]; await state_set(c.from_user.id,'gift_target',data,900)
    await c.message.edit_text('<b>📝 Giftga izoh</b>\n\nIzoh yozing yoki quyidagi tugmani bosing:',reply_markup=kb([[('➡️ Izohsiz yuborish','giftcomment:none')],[('⬅️ Orqaga','gifts')]]),parse_mode='HTML')

@router.callback_query(F.data.startswith('giftcomment:'))
async def giftcomment(c:CallbackQuery,bot:Bot):
    if not await gate(c,bot):return
    data=await state_get(c.from_user.id,'gift_target'); data['comment']=None if c.data.endswith('none') else c.data.split(':',1)[1]; await state_set(c.from_user.id,'gift_target',data,900)
    await gift_preview(c,bot,data)

async def gift_preview(c,bot,data):
    async with Session() as s:g=await s.get(Gift,data['gift_id'])
    unit=await get_decimal('stars_unit_price',Decimal(settings.stars_unit_price)); amount=Decimal(g.stars)*unit
    promo=await get_value(f'user_promo_{c.from_user.id}','')
    await c.message.edit_text(f'<b>📋 GIFT BUYURTMA</b>\n\n🎁 Gift: <b>{g.name}</b>\n⭐️ Qiymat: <b>{g.stars} Stars</b>\n👤 Kimga: <b>{data["target"]}</b>\n🕶 Rejim: <b>{data.get("mode","PROFILE")}</b>\n📝 Izoh: <b>{data.get("comment") or "Yo‘q"}</b>\n💰 Narx: <b>{som(amount)}</b>\n🎟 Promo: <b>{promo or "—"}</b>',reply_markup=confirm('confirm_gift','gifts'),parse_mode='HTML')

@router.callback_query(F.data=='confirm_gift')
async def confirm_gift(c:CallbackQuery,bot:Bot):
    if not await gate(c,bot):return
    data=await state_get(c.from_user.id,'gift_target')
    if not data:return
    async with Session() as s:
        g=await s.get(Gift,data['gift_id']); unit=await get_decimal('stars_unit_price',Decimal(settings.stars_unit_price)); amount=Decimal(g.stars)*unit; promo=await get_value(f'user_promo_{c.from_user.id}','')
        try:o,final,disc=await create_order(s,c.from_user.id,'GIFT',g.stars,data['target'],amount,unit,g.name,data.get('comment'),data.get('mode'),promo); await s.commit()
        except ValueError as e: await c.answer(str(e),show_alert=True); return
    await state_del(c.from_user.id,'gift_target'); await state_set(c.from_user.id,'last_order',{'order_id':o.id},900)
    await send_admin_log(bot,f'🆕 <b>YANGI ORDER</b>\n🧾 <code>{o.public_id}</code>\n👤 User: <code>{c.from_user.id}</code>\n🎁 Gift: <b>{g.name}</b>\n🎯 Target: {data["target"]}\n💰 {som(final)}')
    await c.message.edit_text(f'<b>✅ Buyurtma qabul qilindi!</b>\n\n🧾 <code>{o.public_id}</code>\n🎁 {g.name}\n👤 {data["target"]}\n💰 <b>{som(final)}</b>\n\n⏳ Admin buyurtmani qo‘lda bajaradi.',reply_markup=back('home'),parse_mode='HTML')

@router.message(F.text)
async def text_router(m:Message,bot:Bot):
    if m.from_user.id in settings.admins:return
    state=None
    for key in ['stars_amount','stars_target','gift_target','promo','topup_amount','support']:
        state=await state_get(m.from_user.id,key)
        if state is not None:break
    if state is None:return
    if key=='stars_amount':
        try:n=int(m.text.strip()); assert n>=50
        except: await m.answer('❌ Miqdor noto‘g‘ri. Min: 50 Stars.'); return
        await state_del(m.from_user.id,'stars_amount'); await state_set(m.from_user.id,'stars_target',{'stars':n},600); await m.answer('<b>🎯 Stars kimga yuboriladi?</b>\n\n@username yuboring.',parse_mode='HTML'); return
    if key=='stars_target':
        if not username_ok(m.text): await m.answer('❌ Username <code>@username</code> ko‘rinishida bo‘lsin.',parse_mode='HTML'); return
        st=await state_get(m.from_user.id,'stars_target'); stars=st['stars']; unit=await get_decimal('stars_unit_price',Decimal(settings.stars_unit_price)); amount=Decimal(stars)*unit; promo=await get_value(f'user_promo_{m.from_user.id}','')
        await state_set(m.from_user.id,'stars_order',{'stars':stars,'target':m.text.strip(),'amount':str(amount),'unit':str(unit),'promo':promo},900); await state_del(m.from_user.id,'stars_target')
        await m.answer(f'<b>📋 BUYURTMA</b>\n\n⭐️ Stars: <b>{stars}</b>\n👤 Kimga: <b>{m.text.strip()}</b>\n💰 Narx: <b>{som(amount)}</b>\n🎟 Promo: <b>{promo or "—"}</b>',reply_markup=confirm('confirm_stars','stars'),parse_mode='HTML'); return
    if key=='gift_target':
        if not username_ok(m.text): await m.answer('❌ Username <code>@username</code> ko‘rinishida bo‘lsin.',parse_mode='HTML'); return
        st=await state_get(m.from_user.id,'gift_target'); st['target']=m.text.strip(); await state_set(m.from_user.id,'gift_target',st,900)
        await m.answer('<b>🎁 Qanday yuborilsin?</b>',reply_markup=kb([[('🕶 Anonim','giftmode:ANONYMOUS'),('👤 Profil bilan','giftmode:PROFILE')],[('⬅️ Orqaga','gifts')]]),parse_mode='HTML'); return
    if key=='promo':
        code=m.text.strip().upper()
        async with Session() as s:
            p=await s.scalar(select(PromoCode).where(PromoCode.code==code,PromoCode.active==True))
        if not p: await m.answer('❌ Promo-kod topilmadi.'); return
        await get_value('dummy',''); await state_del(m.from_user.id,'promo'); await __import__('app.services.settings_service',fromlist=['set_value']).set_value(f'user_promo_{m.from_user.id}',code); await m.answer(f'✅ Promo-kod <b>{code}</b> saqlandi. Keyingi xaridda ishlatiladi.',parse_mode='HTML'); return
    if key=='topup_amount':
        try:amount=int(m.text.strip()); assert settings.min_topup<=amount<=settings.max_topup
        except: await m.answer(f'❌ Summa {settings.min_topup:,}–{settings.max_topup:,} oralig‘ida bo‘lsin.'); return
        await state_del(m.from_user.id,'topup_amount'); await state_set(m.from_user.id,'await_receipt',{'amount':amount},1800)
        await m.answer(f'<b>💳 TO‘LOV MA’LUMOTLARI</b>\n\n💰 Summa: <b>{som(amount)}</b>\n💳 Karta: <code>{settings.payment_card}</code>\n👤 Egasi: <b>{settings.payment_card_holder}</b>\n\nTo‘lovdan keyin <b>chek rasmini</b> shu yerga yuboring.\n\n⚠️ Soxta cheklar qabul qilinmaydi.',parse_mode='HTML'); return
    if key=='support':
        async with Session() as s:
            t=SupportTicket(public_id=public_id('TKT'),user_id=m.from_user.id,message=m.text); s.add(t); await s.flush(); s.add(TicketMessage(ticket_id=t.id,actor_id=m.from_user.id,message=m.text)); await s.commit(); tid=t.public_id
        await send_admin_log(bot,f'💬 <b>YANGI TICKET</b>\n🧾 <code>{tid}</code>\n👤 User: <code>{m.from_user.id}</code>\n\n{m.text[:1200]}'); await state_del(m.from_user.id,'support'); await m.answer(f'✅ Xabaringiz adminga yuborildi.\n\n🧾 Ticket: <code>{tid}</code>',parse_mode='HTML'); return

@router.callback_query(F.data=='confirm_stars')
async def confirm_stars(c:CallbackQuery,bot:Bot):
    if not await gate(c,bot):return
    data=await state_get(c.from_user.id,'stars_order');
    if not data:return
    async with Session() as s:
        promo=await get_value(f'user_promo_{c.from_user.id}','')
        try:o,final,disc=await create_order(s,c.from_user.id,'STARS',data['stars'],data['target'],Decimal(data['amount']),Decimal(data['unit']),f'{data["stars"]} Stars',None,None,promo); await s.commit()
        except ValueError as e: await c.answer(str(e),show_alert=True); return
    await state_del(c.from_user.id,'stars_order')
    await send_admin_log(bot,f'🆕 <b>YANGI ORDER</b>\n🧾 <code>{o.public_id}</code>\n👤 User: <code>{c.from_user.id}</code>\n⭐️ Stars: <b>{o.quantity}</b>\n🎯 Target: {o.target_username}\n💰 {som(final)}')
    await c.message.edit_text(f'<b>✅ Buyurtma qabul qilindi!</b>\n\n🧾 <code>{o.public_id}</code>\n⭐️ {o.quantity} Stars\n🎯 {o.target_username}\n💰 <b>{som(final)}</b>\n\n⏳ Admin buyurtmani qo‘lda bajaradi.',reply_markup=back('home'),parse_mode='HTML')

@router.callback_query(F.data=='topup')
async def topup(c:CallbackQuery,bot:Bot):
    if not await gate(c,bot):return
    await state_set(c.from_user.id,'topup_amount',{},900); await c.message.edit_text(f'<b>💳 Hisob to‘ldirish</b>\n\nMinimum: <b>{som(settings.min_topup)}</b>\nMaximum: <b>{som(settings.max_topup)}</b>\n\nTo‘ldirmoqchi bo‘lgan summani yuboring.',reply_markup=back('home'),parse_mode='HTML')

@router.message(F.photo)
async def receipt_photo(m:Message,bot:Bot):
    if m.from_user.id in settings.admins:return
    st=await state_get(m.from_user.id,'await_receipt')
    if not st:return
    file=await bot.get_file(m.photo[-1].file_id); buf=io.BytesIO(); await bot.download_file(file.file_path,buf); data=buf.getvalue(); h=sha256_bytes(data)
    ocr_text=''
    if settings.ocr_enabled:
        try:
            from PIL import Image
            import pytesseract
            ocr_text=pytesseract.image_to_string(Image.open(io.BytesIO(data)))
        except Exception:
            ocr_text=''
    async with Session() as s:
        duplicate=await s.scalar(select(Payment).where(Payment.receipt_hash==h)) is not None
        score,status,reasons=assess_receipt(ocr_text,Decimal(st['amount']),duplicate)
        p=Payment(public_id=public_id('PAY'),user_id=m.from_user.id,amount=Decimal(st['amount']),status='PENDING',receipt_file_id=m.photo[-1].file_id,receipt_hash=h,risk_score=score,risk_status=status,ocr_text=ocr_text)
        s.add(p); await s.flush(); await audit(s,m.from_user.id,'PAYMENT_SUBMITTED','payment',p.public_id,{'risk':status,'reasons':reasons}); await s.commit(); pid=p.id; pub=p.public_id
    await state_del(m.from_user.id,'await_receipt')
    await send_admin_log(bot,f'💳 <b>YANGI TO‘LOV</b>\n🧾 <code>{pub}</code>\n👤 User: <code>{m.from_user.id}</code>\n💰 Summa: <b>{som(st["amount"])}</b>\n⚠️ Risk: <b>{status}</b> ({score})\n{", ".join(reasons) if reasons else "✅ Dastlabki tekshiruv muammo topmadi."}')
    await m.answer(f'✅ <b>Chek qabul qilindi.</b>\n\n🧾 Payment: <code>{pub}</code>\n⏳ Admin tekshiruvidan keyin balansingiz to‘ldiriladi.',parse_mode='HTML')

@router.callback_query(F.data=='promo')
async def promo_start(c:CallbackQuery,bot:Bot):
    if not await gate(c,bot):return
    await state_set(c.from_user.id,'promo',{},600); await c.message.edit_text('<b>🎟 Promo-kod</b>\n\nPromo-kodni yuboring:',reply_markup=back('home'),parse_mode='HTML')
@router.callback_query(F.data=='support')
async def support(c:CallbackQuery,bot:Bot):
    if not await gate(c,bot):return
    await state_set(c.from_user.id,'support',{},900); await c.message.edit_text('<b>💬 Adminga habar</b>\n\nXabaringizni yozing. Admin ID orqali ham javob berishi mumkin.',reply_markup=back('home'),parse_mode='HTML')
@router.callback_query(F.data=='notifications')
async def notifications(c:CallbackQuery,bot:Bot):
    if not await gate(c,bot):return
    async with Session() as s: ns=(await s.execute(select(Notification).where(Notification.user_id==c.from_user.id).order_by(desc(Notification.id)).limit(15))).scalars().all()
    lines=['<b>🔔 Xabarnomalar</b>','']
    for n in ns: lines.append(f'• <b>{n.title}</b>\n{n.body[:180]}')
    if len(lines)==2:lines.append('Hozircha xabarnoma yo‘q.')
    await c.message.edit_text('\n\n'.join(lines),reply_markup=back('home'),parse_mode='HTML')
