from __future__ import annotations

import asyncio
import csv
import io
import json
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from sqlalchemy import desc, func, select

from app.config import settings
from app.db import (
    AuditLog,
    Broadcast,
    Contest,
    ContestParticipant,
    CostLedger,
    Gift,
    Notification,
    Order,
    Payment,
    PromoCode,
    StarPackage,
    SupportTicket,
    SystemError,
    TicketMessage,
    User,
    PriceHistory,
)
from app.services.core import (
    audit,
    cashback_change,
    complete_order,
    daily_stats,
    event,
    ledger_change,
    public_id,
    refund_order,
)
from app.services.receipt_service import make_receipt
from app.services.redis_service import redis, state_del, state_get, state_set
from app.services.settings_service import get_decimal, get_value, set_value
from app.services.telegram_service import public_feed, send_admin_log
from app.utils.text import som
from app.utils.ui import back, kb


router = Router()


def admin_only(uid: int) -> bool:
    """Return True when Telegram user id belongs to configured admins."""
    try:
        return int(uid) in {int(x) for x in settings.admins}
    except (TypeError, ValueError):
        return False


def admin_kb():
    return kb(
        [
            [("🏠 Dashboard", "adash"), ("📦 Buyurtmalar", "a_orders"), ("💳 To‘lovlar", "a_payments")],
            [("🚨 Fraud Center", "a_fraud"), ("🚨 Diqqat markazi", "a_attention"), ("👥 CRM", "a_crm")],
            [("⭐️ Stars", "a_stars"), ("🎁 Gifts", "a_gifts"), ("🏆 Konkurs", "a_contests")],
            [("🎟 Promo", "a_promo"), ("🎁 Cashback", "a_cashback"), ("👥 Referral", "a_referral")],
            [("💰 Moliya", "a_finance"), ("📊 Analitika", "a_analytics"), ("📜 Audit Log", "a_audit")],
            [("👨‍💼 Admin Performance", "a_performance"), ("📤 Export", "a_export")],
            [("💬 Tickets", "a_tickets"), ("📣 Broadcast", "a_broadcast"), ("📨 Userga ID orqali habar", "a_msgid")],
            [("⚙️ Xizmatlar", "a_services"), ("🩺 System Health", "a_health"), ("⚠️ Xatolar", "a_errors")],
            [("👑 Adminlar", "a_admins"), ("🛑 Emergency Mode", "a_emergency")],
        ]
    )


def admin_guard(c: CallbackQuery) -> bool:
    if not admin_only(c.from_user.id):
        asyncio.create_task(c.answer("⛔️ Ruxsat yo‘q", show_alert=True))
        return False
    return True


async def _get_fsm(uid: int):
    value = await redis.get(f"fsm:{uid}")
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="ignore")
    return value.strip() if isinstance(value, str) else value


async def _delete_fsm(uid: int):
    await redis.delete(f"fsm:{uid}")


# =========================================================
# ADMIN ENTRY
# =========================================================


@router.message(Command("admin"))
async def admin_cmd(m: Message):
    if not admin_only(m.from_user.id):
        await m.answer("⛔️ Ruxsat yo‘q.")
        return

    await m.answer(
        "<b>🛠 ADMIN PANEL</b>\n\n"
        "Barcha biznes operatsiyalarini shu yerdan boshqarasiz.",
        reply_markup=admin_kb(),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "admin")
async def admin(c: CallbackQuery):
    if not admin_guard(c):
        return

    await c.message.edit_text(
        "<b>🛠 ADMIN PANEL</b>\n\n"
        "Barcha biznes operatsiyalari shu markazdan boshqariladi.",
        reply_markup=admin_kb(),
        parse_mode="HTML",
    )
    await c.answer()


# =========================================================
# DASHBOARD
# =========================================================


@router.callback_query(F.data.in_({"adash", "a_attention"}))
async def dashboard(c: CallbackQuery, bot: Bot):
    if not admin_guard(c):
        return

    async with Session() as s:
        st = await daily_stats(s)
        active_errors = (
            await s.scalar(
                select(func.count(SystemError.id)).where(SystemError.resolved.is_(False))
            )
            or 0
        )
        fraud = (
            await s.scalar(
                select(func.count(Payment.id)).where(
                    Payment.status == "PENDING",
                    Payment.risk_status == "HIGH",
                )
            )
            or 0
        )

    if c.data == "a_attention":
        txt = (
            "<b>🚨 DIQQAT MARKAZI</b>\n\n"
            f"💳 Kutilayotgan to‘lovlar: <b>{st['pending_pay']}</b>\n"
            f"📦 Jarayondagi buyurtmalar: <b>{st['pending_orders']}</b>\n"
            f"🔴 High-risk payments: <b>{fraud}</b>\n"
            f"⚠️ System errors: <b>{active_errors}</b>"
        )
        markup = kb(
            [
                [("💳 To‘lovlar", "a_payments"), ("📦 Orderlar", "a_orders")],
                [("🚨 Fraud", "a_fraud"), ("⚠️ Xatolar", "a_errors")],
                [("⬅️ Admin", "admin")],
            ]
        )
    else:
        txt = (
            "<b>🏠 ADMIN DASHBOARD</b>\n\n"
            f"👥 Jami userlar: <b>{st['users']}</b>\n"
            f"📦 Bugun bajarilgan: <b>{st['orders']}</b>\n"
            f"💳 Bugungi tushum: <b>{som(st['revenue'])}</b>\n"
            f"💰 Bugungi order tushumi: <b>{som(st['gross'])}</b>\n"
            f"📈 Bugungi net foyda: <b>{som(st['profit'])}</b>\n\n"
            f"🔔 Kutilayotgan payment: <b>{st['pending_pay']}</b>\n"
            f"📦 Kutilayotgan order: <b>{st['pending_orders']}</b>\n"
            f"🚨 High-risk: <b>{fraud}</b>\n"
            f"⚠️ Errors: <b>{active_errors}</b>"
        )
        markup = admin_kb()

    await c.message.edit_text(
        txt,
        reply_markup=markup,
        parse_mode="HTML",
    )
    await c.answer()


# =========================================================
# ORDERS
# =========================================================


@router.callback_query(F.data == "a_orders")
async def orders(c: CallbackQuery):
    if not admin_guard(c):
        return

    async with Session() as s:
        order_list = (
            await s.execute(
                select(Order).order_by(desc(Order.id)).limit(30)
            )
        ).scalars().all()

    rows = []
    for o in order_list:
        rows.append(
            [
                (
                    f"📦 {o.public_id} · {o.order_type} · {som(o.amount)} · {o.status}",
                    f"ord:{o.id}",
                )
            ]
        )

    rows.append([("⬅️ Admin", "admin")])

    await c.message.edit_text(
        "<b>📦 BUYURTMALAR</b>\n\nEng so‘nggi buyurtmalar:",
        reply_markup=kb(rows),
        parse_mode="HTML",
    )
    await c.answer()


@router.callback_query(F.data.startswith("ord:"))
async def order_detail(c: CallbackQuery, bot: Bot | None = None):
    if not admin_guard(c):
        return

    try:
        oid = int(c.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await c.answer("❌ Order ID noto‘g‘ri.", show_alert=True)
        return

    async with Session() as s:
        o = await s.get(Order, oid)
        if not o:
            await c.answer("❌ Order topilmadi.", show_alert=True)
            return

        u = await s.get(User, o.user_id)

        ev = []
        try:
            from app.db import OrderEvent

            ev = (
                await s.execute(
                    select(OrderEvent)
                    .where(OrderEvent.order_id == oid)
                    .order_by(desc(OrderEvent.id))
                    .limit(15)
                )
            ).scalars().all()
        except Exception:
            ev = []

    target = getattr(o, "target_username", None) or "—"
    item_name = getattr(o, "item_name", None) or o.order_type
    comment = getattr(o, "comment", None) or "—"
    discount = getattr(o, "discount", 0) or 0
    cashback_awarded = getattr(o, "cashback_awarded", 0) or 0
    net_profit = getattr(o, "net_profit", 0) or 0

    txt = (
        f"<b>📦 ORDER {o.public_id}</b>\n\n"
        f"👤 User: <code>{o.user_id}</code> @{u.username if u and u.username else '—'}\n"
        f"⭐️/Xizmat: <b>{item_name}</b>\n"
        f"🔢 Miqdor: <b>{o.quantity}</b>\n"
        f"🎯 Target: <b>{target}</b>\n"
        f"💰 Summa: <b>{som(o.amount)}</b>\n"
        f"🏷 Discount: <b>{som(discount)}</b>\n"
        f"🎁 Cashback: <b>{som(cashback_awarded)}</b>\n"
        f"📈 Net profit snapshot: <b>{som(net_profit)}</b>\n"
        f"🕐 Status: <b>{o.status}</b>\n"
        f"📝 Izoh: {comment}"
    )

    if ev:
        timeline = "\n".join(
            f"• {x.created_at:%H:%M:%S} — <b>{x.event}</b>"
            for x in ev[:10]
        )
        txt += f"\n\n<b>📜 Timeline</b>\n{timeline}"

    rows = []

    if o.status == "PENDING":
        rows.append(
            [
                ("🔄 Jarayonga olish", f"ordproc:{oid}"),
                ("✅ Bajarildi", f"orddone:{oid}"),
            ]
        )
    elif o.status == "PROCESSING":
        rows.append(
            [
                ("✅ Bajarildi", f"orddone:{oid}"),
                ("❌ Bajarilmadi", f"ordfail:{oid}"),
            ]
        )

    if o.status in {"PENDING", "PROCESSING"}:
        rows.append([("💰 Refund", f"ordrefund:{oid}")])

    rows.append(
        [
            ("💵 Tannarx", f"ordcost:{oid}"),
            ("📨 Userga habar", f"msguid:{o.user_id}"),
        ]
    )
    rows.append([("⬅️ Orderlar", "a_orders")])

    await c.message.edit_text(
        txt,
        reply_markup=kb(rows),
        parse_mode="HTML",
    )
    await c.answer()


@router.callback_query(F.data.startswith("ordproc:"))
async def order_processing(c: CallbackQuery):
    if not admin_guard(c):
        return

    try:
        oid = int(c.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await c.answer("❌ Order ID noto‘g‘ri.", show_alert=True)
        return

    async with Session() as s:
        o = await s.get(Order, oid)
        if not o:
            await c.answer("❌ Order topilmadi.", show_alert=True)
            return

        if o.status != "PENDING":
            await c.answer("ℹ️ Order allaqachon jarayonda yoki yakunlangan.", show_alert=True)
            return

        o.status = "PROCESSING"
        await event(s, o, c.from_user.id, "ORDER_PROCESSING")
        await audit(
            s,
            c.from_user.id,
            "ORDER_PROCESSING",
            "order",
            o.public_id,
        )
        await s.commit()

    await c.answer("🔄 Jarayonga olindi")
    await order_detail(c)


@router.callback_query(F.data.startswith("orddone:"))
async def order_done(c: CallbackQuery, bot: Bot):
    if not admin_guard(c):
        return

    try:
        oid = int(c.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await c.answer("❌ Order ID noto‘g‘ri.", show_alert=True)
        return

    async with Session() as s:
        o = await s.get(Order, oid)
        if not o:
            await c.answer("❌ Order topilmadi.", show_alert=True)
            return

        u = await s.get(User, o.user_id)
        cbpct = await get_decimal(
            "cashback_percent",
            Decimal(settings.cashback_percent),
        )

        try:
            profit = await complete_order(
                s,
                o,
                c.from_user.id,
                cbpct,
            )
            await s.commit()
        except ValueError as e:
            await s.rollback()
            await c.answer(str(e), show_alert=True)
            return
        except Exception:
            await s.rollback()
            await c.answer(
                "❌ Buyurtmani yakunlashda xato.",
                show_alert=True,
            )
            return

    try:
        receipt = make_receipt(
            o,
            u,
            settings.bot_name,
        )
        await bot.send_photo(
            o.user_id,
            receipt,
            caption=(
                "<b>✅ Buyurtma bajarildi!</b>\n\n"
                f"🧾 <code>{o.public_id}</code>\n"
                f"🎁 Cashback: <b>{som(o.cashback_awarded)}</b>"
            ),
            parse_mode="HTML",
        )
    except Exception:
        pass

    try:
        await public_feed(
            bot,
            "🎉 <b>BUYURTMA BAJARILDI</b>\n\n"
            f"🧾 <code>{o.public_id}</code>\n"
            f"⭐️/🎁: <b>{o.quantity} {o.order_type}</b>\n"
            f"➡️ {getattr(o, 'target_username', None) or '—'}\n"
            "✅ Ishonchli xizmat!",
        )
    except Exception:
        pass

    try:
        await send_admin_log(
            bot,
            "✅ ORDER COMPLETED\n"
            f"{o.public_id}\n"
            f"Admin: {c.from_user.id}\n"
            f"Cashback: {som(getattr(o, 'cashback_awarded', 0) or 0)}",
        )
    except Exception:
        pass

    await c.answer("✅ Bajarildi")
    await order_detail(c, bot)


@router.callback_query(F.data.startswith("ordfail:"))
async def order_fail(c: CallbackQuery, bot: Bot):
    if not admin_guard(c):
        return

    try:
        oid = int(c.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await c.answer("❌ Order ID noto‘g‘ri.", show_alert=True)
        return

    async with Session() as s:
        o = await s.get(Order, oid)
        if not o:
            await c.answer("❌ Order topilmadi.", show_alert=True)
            return

        amount = o.amount
        public = o.public_id
        uid = o.user_id

        try:
            await refund_order(
                s,
                o,
                c.from_user.id,
                "Admin marked order failed",
            )
            await s.commit()
        except ValueError as e:
            await s.rollback()
            await c.answer(str(e), show_alert=True)
            return
        except Exception:
            await s.rollback()
            await c.answer(
                "❌ Refund qilishda xato.",
                show_alert=True,
            )
            return

    try:
        await bot.send_message(
            uid,
            "<b>❌ Buyurtma bajarilmadi</b>\n\n"
            f"🧾 <code>{public}</code>\n"
            f"💰 <b>{som(amount)}</b> balansingizga qaytarildi.",
            parse_mode="HTML",
        )
    except Exception:
        pass

    await c.answer("✅ Refund qilindi")
    await order_detail(c, bot)


@router.callback_query(F.data.startswith("ordrefund:"))
async def order_refund(c: CallbackQuery, bot: Bot):
    if not admin_guard(c):
        return
    # Refund va fail bir xil biznes operatsiyasidan foydalanadi.
    await order_fail(c, bot)


# =========================================================
# PAYMENTS
# =========================================================


@router.callback_query(F.data == "a_payments")
async def payments(c: CallbackQuery):
    if not admin_guard(c):
        return

    async with Session() as s:
        payments_list = (
            await s.execute(
                select(Payment).order_by(desc(Payment.id)).limit(30)
            )
        ).scalars().all()

    rows = [
        [
            (
                f"💳 {p.public_id} · {som(p.amount)} · {p.status} · {p.risk_status}",
                f"pay:{p.id}",
            )
        ]
        for p in payments_list
    ]
    rows.append([("⬅️ Admin", "admin")])

    await c.message.edit_text(
        "<b>💳 TO‘LOVLAR</b>\n\n"
        "Pending, approved va rejected to‘lovlar:",
        reply_markup=kb(rows),
        parse_mode="HTML",
    )
    await c.answer()


@router.callback_query(F.data.startswith("pay:"))
async def pay_detail(c: CallbackQuery, bot: Bot):
    if not admin_guard(c):
        return

    try:
        pid = int(c.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await c.answer("❌ Payment ID noto‘g‘ri.", show_alert=True)
        return

    async with Session() as s:
        p = await s.get(Payment, pid)
        if not p:
            await c.answer("❌ Payment topilmadi.", show_alert=True)
            return

        txt = (
            f"<b>💳 PAYMENT {p.public_id}</b>\n\n"
            f"👤 User: <code>{p.user_id}</code>\n"
            f"💰 Summa: <b>{som(p.amount)}</b>\n"
            f"🟡 Status: <b>{p.status}</b>\n"
            f"🚨 Risk: <b>{p.risk_status} ({p.risk_score})</b>\n"
            f"🔎 OCR summa: <b>{som(p.ocr_amount) if p.ocr_amount else '—'}</b>"
        )

        receipt_file_id = p.receipt_file_id
        risk_status = p.risk_status

    rows = []
    if p.status == "PENDING":
        rows.append(
            [
                ("✅ Tasdiqlash", f"payok:{pid}"),
                ("❌ Rad etish", f"payno:{pid}"),
            ]
        )

    rows.append([("📨 Userga ID orqali habar", f"msguid:{p.user_id}")])
    rows.append([("⬅️ To‘lovlar", "a_payments")])

    await c.message.edit_text(
        txt,
        reply_markup=kb(rows),
        parse_mode="HTML",
    )

    if receipt_file_id:
        try:
            await bot.send_photo(
                c.from_user.id,
                receipt_file_id,
                caption=f"<b>{p.public_id}</b>\nRisk: {risk_status}",
                parse_mode="HTML",
            )
        except Exception:
            pass

    await c.answer()


@router.callback_query(F.data.startswith("payok:"))
async def payok(c: CallbackQuery, bot: Bot):
    if not admin_guard(c):
        return

    try:
        pid = int(c.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await c.answer("❌ Payment ID noto‘g‘ri.", show_alert=True)
        return

    async with Session() as s:
        p = await s.get(Payment, pid)
        if not p:
            await c.answer("❌ Payment topilmadi.", show_alert=True)
            return

        if p.status != "PENDING":
            await c.answer("ℹ️ Allaqachon ko‘rilgan.", show_alert=True)
            return

        p.status = "APPROVED"
        p.approved_at = datetime.now(timezone.utc)
        p.approved_by = c.from_user.id

        try:
            bal = await ledger_change(
                s,
                p.user_id,
                p.amount,
                "TOPUP",
                p.public_id,
                "Admin approved payment",
            )

            await audit(
                s,
                c.from_user.id,
                "PAYMENT_APPROVED",
                "payment",
                p.public_id,
                {"balance_after": str(bal)},
            )
            await s.commit()
        except Exception:
            await s.rollback()
            await c.answer("❌ To‘lovni tasdiqlashda xato.", show_alert=True)
            return

        uid = p.user_id
        amt = p.amount

    try:
        await bot.send_message(
            uid,
            "<b>✅ To‘lovingiz tasdiqlandi!</b>\n\n"
            f"💰 +<b>{som(amt)}</b>\n"
            f"💳 Yangi balans: <b>{som(bal)}</b>",
            parse_mode="HTML",
        )
    except Exception:
        pass

    await c.answer("✅ Tasdiqlandi")
    await payments(c)


@router.callback_query(F.data.startswith("payno:"))
async def payno(c: CallbackQuery, bot: Bot):
    if not admin_guard(c):
        return

    try:
        pid = int(c.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await c.answer("❌ Payment ID noto‘g‘ri.", show_alert=True)
        return

    async with Session() as s:
        p = await s.get(Payment, pid)
        if not p:
            await c.answer("❌ Payment topilmadi.", show_alert=True)
            return

        if p.status != "PENDING":
            await c.answer("ℹ️ Allaqachon ko‘rilgan.", show_alert=True)
            return

        p.status = "REJECTED"
        p.approved_by = c.from_user.id
        public = p.public_id
        uid = p.user_id

        try:
            await audit(
                s,
                c.from_user.id,
                "PAYMENT_REJECTED",
                "payment",
                p.public_id,
            )
            await s.commit()
        except Exception:
            await s.rollback()
            await c.answer("❌ To‘lovni rad etishda xato.", show_alert=True)
            return

    try:
        await bot.send_message(
            uid,
            f"❌ <b>{public}</b> to‘lovingiz rad etildi. Supportga murojaat qilishingiz mumkin.",
            parse_mode="HTML",
        )
    except Exception:
        pass

    await c.answer("❌ Rad etildi")
    await payments(c)


# =========================================================
# STARS
# =========================================================


@router.callback_query(F.data == "a_stars")
async def stars_admin(c: CallbackQuery):
    if not admin_guard(c):
        return

    unit = await get_decimal(
        "stars_unit_price",
        Decimal(settings.stars_unit_price),
    )

    async with Session() as s:
        packs = (
            await s.execute(
                select(StarPackage).order_by(StarPackage.stars)
            )
        ).scalars().all()

    txt = (
        "<b>⭐️ STARS BOSHQARUVI</b>\n\n"
        f"1 Stars: <b>{som(unit)}</b>\n\n"
        "Paketlar:\n"
        + "\n".join(
            f'• {p.stars} — {"ON" if p.active else "OFF"}'
            for p in packs
        )
    )

    await c.message.edit_text(
        txt,
        reply_markup=kb(
            [
                [("💰 Narxni o‘zgartirish", "astarprice"), ("➕ Paket", "astarpack")],
                [("⬅️ Admin", "admin")],
            ]
        ),
        parse_mode="HTML",
    )
    await c.answer()


@router.callback_query(F.data == "astarprice")
async def astarprice(c: CallbackQuery):
    if not admin_guard(c):
        return

    await redis.set(
        f"fsm:{c.from_user.id}",
        "a_price",
        ex=600,
    )
    await c.message.edit_text(
        "<b>💰 1 Stars narxini yuboring</b>",
        reply_markup=back("a_stars"),
        parse_mode="HTML",
    )
    await c.answer()


@router.callback_query(F.data == "astarpack")
async def astarpack(c: CallbackQuery):
    if not admin_guard(c):
        return

    await redis.set(
        f"fsm:{c.from_user.id}",
        "a_pack",
        ex=600,
    )
    await c.message.edit_text(
        "<b>➕ Stars paket</b>\n\n"
        "Masalan: <code>250</code>",
        reply_markup=back("a_stars"),
        parse_mode="HTML",
    )
    await c.answer()


# =========================================================
# GIFTS
# =========================================================


@router.callback_query(F.data == "a_gifts")
async def gifts_admin(c: CallbackQuery):
    if not admin_guard(c):
        return

    async with Session() as s:
        gifts = (
            await s.execute(
                select(Gift).order_by(Gift.id)
            )
        ).scalars().all()

    unit = await get_decimal(
        "stars_unit_price",
        Decimal(settings.stars_unit_price),
    )

    rows = []
    for g in gifts:
        rows.append(
            [
                (
                    f'🎁 {g.name} · {g.stars} · {som(g.stars * unit)} · {"ON" if g.active else "OFF"}',
                    f"gt:{g.id}",
                )
            ]
        )

    rows.append(
        [("➕ Gift qo‘shish", "giftadd"), ("⬅️ Admin", "admin")]
    )

    await c.message.edit_text(
        "<b>🎁 GIFT BOSHQARUVI</b>\n\n"
        "Dinamik narx Stars narxidan hisoblanadi.",
        reply_markup=kb(rows),
        parse_mode="HTML",
    )
    await c.answer()


@router.callback_query(F.data.startswith("gt:"))
async def gift_toggle(c: CallbackQuery):
    if not admin_guard(c):
        return

    try:
        gid = int(c.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await c.answer("❌ Gift ID noto‘g‘ri.", show_alert=True)
        return

    async with Session() as s:
        g = await s.get(Gift, gid)
        if not g:
            await c.answer("❌ Gift topilmadi.", show_alert=True)
            return

        g.active = not g.active
        await audit(
            s,
            c.from_user.id,
            "GIFT_TOGGLED",
            "gift",
            str(g.id),
            {"active": g.active},
        )
        await s.commit()

    await c.answer("✅ Holat o‘zgardi")
    await gifts_admin(c)


@router.callback_query(F.data == "giftadd")
async def giftadd(c: CallbackQuery):
    if not admin_guard(c):
        return

    await redis.set(
        f"fsm:{c.from_user.id}",
        "a_giftadd",
        ex=900,
    )
    await c.message.edit_text(
        "<b>➕ Gift qo‘shish</b>\n\n"
        "Format: <code>telegram_gift_id|Nomi|Stars</code>",
        reply_markup=back("a_gifts"),
        parse_mode="HTML",
    )
    await c.answer()


# =========================================================
# CONTESTS
# =========================================================


@router.callback_query(F.data == "a_contests")
async def contests(c: CallbackQuery):
    if not admin_guard(c):
        return

    async with Session() as s:
        contest_list = (
            await s.execute(
                select(Contest)
                .order_by(desc(Contest.id))
                .limit(20)
            )
        ).scalars().all()

    rows = [[("➕ Konkurs qo‘shish", "contestadd")]]
    for x in contest_list:
        rows.append(
            [
                (
                    f'🏆 {x.title} · {"ON" if x.active else "OFF"} · {"DONE" if x.finished else "LIVE"}',
                    f"ct:{x.id}",
                )
            ]
        )

    rows.append([("⬅️ Admin", "admin")])

    await c.message.edit_text(
        "<b>🏆 KONKURSLAR</b>",
        reply_markup=kb(rows),
        parse_mode="HTML",
    )
    await c.answer()


@router.callback_query(F.data == "contestadd")
async def contestadd(c: CallbackQuery):
    if not admin_guard(c):
        return

    await redis.set(
        f"fsm:{c.from_user.id}",
        "a_contest",
        ex=900,
    )
    await c.message.edit_text(
        "<b>🏆 Konkurs qo‘shish</b>\n\n"
        "Format:\n"
        "<code>nom|tavsif|sovrin|soat|g‘oliblar</code>",
        reply_markup=back("a_contests"),
        parse_mode="HTML",
    )
    await c.answer()


@router.callback_query(F.data.startswith("ct:"))
async def contest_toggle(c: CallbackQuery):
    if not admin_guard(c):
        return

    try:
        cid = int(c.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await c.answer("❌ Contest ID noto‘g‘ri.", show_alert=True)
        return

    async with Session() as s:
        x = await s.get(Contest, cid)
        if not x:
            await c.answer("❌ Konkurs topilmadi.", show_alert=True)
            return

        x.active = not x.active
        await audit(
            s,
            c.from_user.id,
            "CONTEST_TOGGLED",
            "contest",
            str(cid),
            {"active": x.active},
        )
        await s.commit()

    await contests(c)
    await c.answer("✅ Holat o‘zgardi")


# =========================================================
# CRM / USER MESSAGES
# =========================================================


@router.callback_query(F.data == "a_crm")
async def crm(c: CallbackQuery):
    if not admin_guard(c):
        return

    await redis.set(
        f"fsm:{c.from_user.id}",
        "a_crm",
        ex=600,
    )
    await c.message.edit_text(
        "<b>👥 CRM</b>\n\n"
        "Telegram ID yoki @username yuboring.",
        reply_markup=back("admin"),
        parse_mode="HTML",
    )
    await c.answer()


@router.callback_query(F.data == "a_msgid")
async def msgid(c: CallbackQuery):
    if not admin_guard(c):
        return

    await redis.set(
        f"fsm:{c.from_user.id}",
        "a_msgid",
        ex=900,
    )
    await c.message.edit_text(
        "<b>📨 Userga ID orqali habar</b>\n\n"
        "Foydalanuvchining Telegram ID raqamini yuboring.",
        reply_markup=back("admin"),
        parse_mode="HTML",
    )
    await c.answer()


@router.callback_query(F.data.startswith("msguid:"))
async def msguid(c: CallbackQuery):
    if not admin_guard(c):
        return

    try:
        uid = int(c.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await c.answer("❌ User ID noto‘g‘ri.", show_alert=True)
        return

    await redis.set(
        f"fsm:{c.from_user.id}",
        f"a_msgtext:{uid}",
        ex=900,
    )
    await c.message.edit_text(
        f"<b>📨 User ID: <code>{uid}</code></b>\n\n"
        "Yuboriladigan HTML xabarni yozing.",
        reply_markup=back("admin"),
        parse_mode="HTML",
    )
    await c.answer()


# =========================================================
# FINANCE / ANALYTICS / AUDIT
# =========================================================


@router.callback_query(F.data == "a_finance")
async def finance(c: CallbackQuery):
    if not admin_guard(c):
        return

    async with Session() as s:
        st = await daily_stats(s)

    await c.message.edit_text(
        "<b>💰 MOLIYA</b>\n\n"
        f"💳 Tushum: <b>{som(st['revenue'])}</b>\n"
        f"📦 Order gross: <b>{som(st['gross'])}</b>\n"
        f"📈 Net profit: <b>{som(st['profit'])}</b>\n"
        f"📦 Completed: <b>{st['orders']}</b>\n\n"
        "Formula: Revenue − cost − cashback − discount − refund.",
        reply_markup=back("admin"),
        parse_mode="HTML",
    )
    await c.answer()


@router.callback_query(F.data == "a_analytics")
async def analytics(c: CallbackQuery):
    if not admin_guard(c):
        return

    async with Session() as s:
        users = await s.scalar(select(func.count(User.id))) or 0
        buyers = (
            await s.scalar(
                select(func.count(func.distinct(Order.user_id))).where(
                    Order.status == "COMPLETED"
                )
            )
            or 0
        )
        avg = (
            await s.scalar(
                select(func.avg(Order.amount)).where(
                    Order.status == "COMPLETED"
                )
            )
            or 0
        )
        top = (
            await s.execute(
                select(
                    Order.item_name,
                    func.count(Order.id),
                )
                .where(Order.status == "COMPLETED")
                .group_by(Order.item_name)
                .order_by(desc(func.count(Order.id)))
                .limit(5)
            )
        ).all()

    conversion = (buyers / users * 100) if users else 0

    txt = (
        "<b>📊 ANALITIKA</b>\n\n"
        f"👥 Users: <b>{users}</b>\n"
        f"🛒 Buyers: <b>{buyers}</b>\n"
        f"🎯 Conversion: <b>{conversion:.2f}%</b>\n"
        f"🧮 Avg order: <b>{som(avg)}</b>\n\n"
        "<b>Top services</b>\n"
        + "\n".join(
            f'• {name or "—"}: {count}'
            for name, count in top
        )
    )

    await c.message.edit_text(
        txt,
        reply_markup=back("admin"),
        parse_mode="HTML",
    )
    await c.answer()


@router.callback_query(F.data == "a_audit")
async def audit_logs(c: CallbackQuery):
    if not admin_guard(c):
        return

    async with Session() as s:
        audit_list = (
            await s.execute(
                select(AuditLog)
                .order_by(desc(AuditLog.id))
                .limit(30)
            )
        ).scalars().all()

    txt = "<b>📜 AUDIT LOG</b>\n\n"
    if audit_list:
        txt += "\n".join(
            f"• {x.created_at:%d.%m %H:%M} · {x.action} · "
            f"{x.entity or ''} · {x.actor_id or 'system'}"
            for x in audit_list
        )
    else:
        txt += "✅ Hozircha audit log yo‘q."

    await c.message.edit_text(
        txt,
        reply_markup=back("admin"),
        parse_mode="HTML",
    )
    await c.answer()


# =========================================================
# TICKETS
# =========================================================


@router.callback_query(F.data == "a_tickets")
async def tickets(c: CallbackQuery):
    if not admin_guard(c):
        return

    async with Session() as s:
        ticket_list = (
            await s.execute(
                select(SupportTicket)
                .order_by(desc(SupportTicket.id))
                .limit(30)
            )
        ).scalars().all()

    rows = [
        [
            (
                f"💬 {t.public_id} · {t.status} · user {t.user_id}",
                f"ticket:{t.id}",
            )
        ]
        for t in ticket_list
    ]
    rows.append([("⬅️ Admin", "admin")])

    await c.message.edit_text(
        "<b>💬 TICKETS</b>",
        reply_markup=kb(rows),
        parse_mode="HTML",
    )
    await c.answer()


@router.callback_query(F.data.startswith("ticket:"))
async def ticket_detail(c: CallbackQuery):
    if not admin_guard(c):
        return

    try:
        tid = int(c.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await c.answer("❌ Ticket ID noto‘g‘ri.", show_alert=True)
        return

    async with Session() as s:
        t = await s.get(SupportTicket, tid)
        if not t:
            await c.answer("❌ Ticket topilmadi.", show_alert=True)
            return

        txt = (
            f"<b>💬 {t.public_id}</b>\n\n"
            f"👤 User: <code>{t.user_id}</code>\n"
            f"📌 {t.status}\n\n"
            f"{t.message}"
        )

    await c.message.edit_text(
        txt,
        reply_markup=kb(
            [
                [
                    ("📨 Javob berish", f"msguid:{t.user_id}"),
                    ("✅ Yopish", f"tclose:{tid}"),
                ],
                [("⬅️ Tickets", "a_tickets")],
            ]
        ),
        parse_mode="HTML",
    )
    await c.answer()


@router.callback_query(F.data.startswith("tclose:"))
async def ticket_close(c: CallbackQuery):
    if not admin_guard(c):
        return

    try:
        tid = int(c.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await c.answer("❌ Ticket ID noto‘g‘ri.", show_alert=True)
        return

    async with Session() as s:
        t = await s.get(SupportTicket, tid)
        if not t:
            await c.answer("❌ Ticket topilmadi.", show_alert=True)
            return

        t.status = "CLOSED"
        t.closed_at = datetime.now(timezone.utc)
        await audit(
            s,
            c.from_user.id,
            "TICKET_CLOSED",
            "ticket",
            t.public_id,
        )
        await s.commit()

    await tickets(c)


# =========================================================
# BROADCAST / PROMO / CASHBACK / REFERRAL
# =========================================================


@router.callback_query(F.data == "a_broadcast")
async def broadcast(c: CallbackQuery):
    if not admin_guard(c):
        return

    await redis.set(
        f"fsm:{c.from_user.id}",
        "a_broadcast",
        ex=900,
    )
    await c.message.edit_text(
        "<b>📣 BROADCAST</b>\n\n"
        "Avval format yuboring:\n"
        "<code>SEGMENT|HTML MATN</code>\n\n"
        "Segment: ALL, NEW, BUYERS, VIP",
        reply_markup=back("admin"),
        parse_mode="HTML",
    )
    await c.answer()


@router.callback_query(F.data == "a_promo")
async def promo(c: CallbackQuery):
    if not admin_guard(c):
        return

    await redis.set(
        f"fsm:{c.from_user.id}",
        "a_promo",
        ex=900,
    )
    await c.message.edit_text(
        "<b>🎟 PROMO-KOD</b>\n\n"
        "Format:\n"
        "<code>KOD|FIXED|5000|100|0</code>\n"
        "yoki\n"
        "<code>KOD|PERCENT|10|100|50000</code>\n\n"
        "max uses va minimal order.",
        reply_markup=back("admin"),
        parse_mode="HTML",
    )
    await c.answer()


@router.callback_query(F.data == "a_cashback")
async def cashback_admin(c: CallbackQuery):
    if not admin_guard(c):
        return

    cb = await get_decimal(
        "cashback_percent",
        Decimal(settings.cashback_percent),
    )
    await redis.set(
        f"fsm:{c.from_user.id}",
        "a_cashback",
        ex=600,
    )
    await c.message.edit_text(
        f"<b>🎁 CASHBACK</b>\n\n"
        f"Amaldagi: <b>{cb}%</b>\n\n"
        "Yangi foizni yuboring.",
        reply_markup=back("admin"),
        parse_mode="HTML",
    )
    await c.answer()


@router.callback_query(F.data == "a_referral")
async def referral_admin(c: CallbackQuery):
    if not admin_guard(c):
        return

    rb = await get_decimal(
        "referral_bonus",
        Decimal(settings.referral_bonus),
    )
    await redis.set(
        f"fsm:{c.from_user.id}",
        "a_referral",
        ex=600,
    )
    await c.message.edit_text(
        f"<b>👥 REFERRAL</b>\n\n"
        f"Bonus: <b>{som(rb)}</b>\n\n"
        "Yangi bonusni yuboring.",
        reply_markup=back("admin"),
        parse_mode="HTML",
    )
    await c.answer()


# =========================================================
# SERVICES
# =========================================================


@router.callback_query(F.data == "a_services")
async def services(c: CallbackQuery):
    if not admin_guard(c):
        return

    keys = [
        "service_stars",
        "service_gifts",
        "service_topup",
        "service_contest",
        "service_promo",
    ]

    lines = ["<b>⚙️ XIZMATLAR</b>", ""]
    rows = []

    for key in keys:
        value = str(await get_value(key, "true")).lower() in {
            "true",
            "1",
            "on",
            "yes",
        }
        name = key.replace("service_", "").upper()
        lines.append(
            f'{name}: <b>{"🟢 ON" if value else "🔴 OFF"}</b>'
        )
        rows.append(
            [
                (
                    f'{"🟢" if value else "🔴"} {name}',
                    f"toggle:{key}",
                )
            ]
        )

    rows.append([("⬅️ Admin", "admin")])

    await c.message.edit_text(
        "\n".join(lines),
        reply_markup=kb(rows),
        parse_mode="HTML",
    )
    await c.answer()


async def audit_value(admin_id: int, key: str, value: bool):
    async with Session() as s:
        await audit(
            s,
            admin_id,
            "SERVICE_TOGGLED",
            "setting",
            key,
            {"value": value},
        )
        await s.commit()


@router.callback_query(F.data.startswith("toggle:"))
async def toggle(c: CallbackQuery):
    if not admin_guard(c):
        return

    key = c.data.split(":", 1)[1]
    current = str(await get_value(key, "true")).lower() in {
        "true",
        "1",
        "on",
        "yes",
    }

    await set_value(
        key,
        "false" if current else "true",
    )
    await audit_value(
        c.from_user.id,
        key,
        not current,
    )

    await services(c)


# =========================================================
# SYSTEM HEALTH / ERRORS / FRAUD / ADMINS / EMERGENCY
# =========================================================


@router.callback_query(F.data == "a_health")
async def health(c: CallbackQuery):
    if not admin_guard(c):
        return

    db = "🟢"
    rd = "🟢"

    try:
        async with Session() as s:
            await s.scalar(select(1))
    except Exception:
        db = "🔴"

    try:
        result = redis.ping()
        if asyncio.iscoroutine(result):
            await result
    except Exception:
        rd = "🔴"

    await c.message.edit_text(
        "<b>🩺 SYSTEM HEALTH</b>\n\n"
        f"PostgreSQL: {db}\n"
        f"Redis: {rd}\n"
        "Worker: 🟢 (service-level)\n"
        "Telegram: 🟢 (worker running)",
        reply_markup=back("admin"),
        parse_mode="HTML",
    )
    await c.answer()


@router.callback_query(F.data == "a_errors")
async def errors(c: CallbackQuery):
    if not admin_guard(c):
        return

    async with Session() as s:
        error_list = (
            await s.execute(
                select(SystemError)
                .where(SystemError.resolved.is_(False))
                .order_by(desc(SystemError.id))
                .limit(20)
            )
        ).scalars().all()

    if error_list:
        txt = (
            "<b>⚠️ XATOLAR</b>\n\n"
            + "\n".join(
                f"• <code>{x.public_id}</code> · {x.module}\n"
                f"{x.message[:140]}"
                for x in error_list
            )
        )
    else:
        txt = (
            "<b>⚠️ XATOLAR</b>\n\n"
            "✅ Faol xatolar yo‘q."
        )

    await c.message.edit_text(
        txt,
        reply_markup=back("admin"),
        parse_mode="HTML",
    )
    await c.answer()


@router.callback_query(F.data == "a_fraud")
async def fraud_center(c: CallbackQuery):
    if not admin_guard(c):
        return

    async with Session() as s:
        payment_list = (
            await s.execute(
                select(Payment)
                .where(
                    Payment.status == "PENDING",
                    Payment.risk_status.in_(["HIGH", "MEDIUM"]),
                )
                .order_by(desc(Payment.id))
                .limit(20)
            )
        ).scalars().all()

    rows = [
        [
            (
                f"🚨 {p.public_id} · {p.risk_status} {p.risk_score} · {som(p.amount)}",
                f"pay:{p.id}",
            )
        ]
        for p in payment_list
    ]
    rows.append([("⬅️ Admin", "admin")])

    await c.message.edit_text(
        "<b>🚨 FRAUD CENTER</b>\n\n"
        "High/Medium risk pending payments:",
        reply_markup=kb(rows),
        parse_mode="HTML",
    )
    await c.answer()


@router.callback_query(F.data == "a_admins")
async def admins(c: CallbackQuery):
    if not admin_guard(c):
        return

    admin_ids = sorted(int(x) for x in settings.admins)
    text = "<b>👑 ADMINLAR</b>\n\n"
    text += "\n".join(
        f"• <code>{x}</code>" for x in admin_ids
    )
    text += (
        "\n\nRollarni productionda RBAC jadvali orqali "
        "kengaytirish mumkin."
    )

    await c.message.edit_text(
        text,
        reply_markup=back("admin"),
        parse_mode="HTML",
    )
    await c.answer()


@router.callback_query(F.data == "a_emergency")
async def emergency(c: CallbackQuery):
    if not admin_guard(c):
        return

    current = str(
        await get_value("emergency_mode", "false")
    ).lower() == "true"

    new_value = not current
    await set_value(
        "emergency_mode",
        "true" if new_value else "false",
    )
    await audit_value(
        c.from_user.id,
        "emergency_mode",
        new_value,
    )

    await c.message.edit_text(
        "<b>🛑 EMERGENCY MODE</b>\n\n"
        f'Hozir: <b>{"🔴 ON" if new_value else "🟢 OFF"}</b>\n\n'
        "ON bo‘lsa yangi moliyaviy orderlar vaqtincha bloklanadi.",
        reply_markup=back("admin"),
        parse_mode="HTML",
    )
    await c.answer()


# =========================================================
# ORDER COST / PERFORMANCE / EXPORT
# =========================================================


@router.callback_query(F.data.startswith("ordcost:"))
async def order_cost(c: CallbackQuery):
    if not admin_guard(c):
        return

    try:
        oid = int(c.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await c.answer("❌ Order ID noto‘g‘ri.", show_alert=True)
        return

    await redis.set(
        f"fsm:{c.from_user.id}",
        f"a_cost:{oid}",
        ex=600,
    )
    await c.message.edit_text(
        "<b>💵 Buyurtma tannarxi</b>\n\n"
        "Tannarxni so‘mda yuboring. Bu net foyda hisobida ishlatiladi.",
        reply_markup=back(f"ord:{oid}"),
        parse_mode="HTML",
    )
    await c.answer()


@router.callback_query(F.data == "a_performance")
async def performance(c: CallbackQuery):
    if not admin_guard(c):
        return

    async with Session() as s:
        perf_rows = (
            await s.execute(
                select(
                    Order.completed_by,
                    func.count(Order.id),
                    func.coalesce(func.sum(Order.amount), 0),
                )
                .where(Order.status == "COMPLETED")
                .group_by(Order.completed_by)
                .order_by(desc(func.count(Order.id)))
            )
        ).all()

    if perf_rows:
        txt = (
            "<b>👨‍💼 ADMIN PERFORMANCE</b>\n\n"
            + "\n".join(
                f"• Admin <code>{admin_id or '—'}</code> · "
                f"{count} order · {som(total)}"
                for admin_id, count, total in perf_rows
            )
        )
    else:
        txt = (
            "<b>👨‍💼 ADMIN PERFORMANCE</b>\n\n"
            "Hali bajarilgan order yo‘q."
        )

    await c.message.edit_text(
        txt,
        reply_markup=back("admin"),
        parse_mode="HTML",
    )
    await c.answer()


@router.callback_query(F.data == "a_export")
async def export_center(c: CallbackQuery):
    if not admin_guard(c):
        return

    await c.message.edit_text(
        "<b>📤 EXPORT</b>\n\n"
        "Qaysi ma’lumotni olish kerak?",
        reply_markup=kb(
            [
                [("📦 Orders", "export:orders"), ("💳 Payments", "export:payments")],
                [("👥 Users", "export:users")],
                [("⬅️ Admin", "admin")],
            ]
        ),
        parse_mode="HTML",
    )
    await c.answer()


@router.callback_query(F.data.startswith("export:"))
async def export_data(c: CallbackQuery):
    if not admin_guard(c):
        return

    kind = c.data.split(":", 1)[1]
    if kind not in {"orders", "payments", "users"}:
        await c.answer("❌ Export turi noto‘g‘ri.", show_alert=True)
        return

    out = io.StringIO()
    writer = csv.writer(out)

    async with Session() as s:
        if kind == "orders":
            rows = (
                await s.execute(
                    select(Order).order_by(Order.id)
                )
            ).scalars().all()

            writer.writerow(
                [
                    "public_id",
                    "user_id",
                    "type",
                    "status",
                    "quantity",
                    "target",
                    "amount",
                    "discount",
                    "profit",
                    "created_at",
                    "completed_at",
                ]
            )

            for o in rows:
                writer.writerow(
                    [
                        o.public_id,
                        o.user_id,
                        o.order_type,
                        o.status,
                        o.quantity,
                        getattr(o, "target_username", None),
                        o.amount,
                        getattr(o, "discount", 0),
                        getattr(o, "net_profit", 0),
                        getattr(o, "created_at", None),
                        getattr(o, "completed_at", None),
                    ]
                )

            name = "orders.csv"

        elif kind == "payments":
            rows = (
                await s.execute(
                    select(Payment).order_by(Payment.id)
                )
            ).scalars().all()

            writer.writerow(
                [
                    "public_id",
                    "user_id",
                    "amount",
                    "status",
                    "risk",
                    "created_at",
                    "approved_at",
                ]
            )

            for p in rows:
                writer.writerow(
                    [
                        p.public_id,
                        p.user_id,
                        p.amount,
                        p.status,
                        p.risk_status,
                        getattr(p, "created_at", None),
                        getattr(p, "approved_at", None),
                    ]
                )

            name = "payments.csv"

        else:
            rows = (
                await s.execute(
                    select(User).order_by(User.id)
                )
            ).scalars().all()

            writer.writerow(
                [
                    "id",
                    "username",
                    "balance",
                    "cashback",
                    "referrals",
                    "lifetime_spent",
                    "status",
                    "created_at",
                ]
            )

            for u in rows:
                writer.writerow(
                    [
                        u.id,
                        u.username,
                        u.balance,
                        u.cashback,
                        u.referral_count,
                        u.lifetime_spent,
                        u.account_status,
                        getattr(u, "created_at", None),
                    ]
                )

            name = "users.csv"

    await c.message.answer_document(
        BufferedInputFile(
            out.getvalue().encode("utf-8-sig"),
            filename=name,
        ),
        caption=f"<b>📤 {name}</b>",
        parse_mode="HTML",
    )
    await c.answer("✅ Tayyor")


# =========================================================
# ADMIN TEXT / FSM
# =========================================================


@router.message(F.text)
async def admin_text(m: Message, bot: Bot):
    if not admin_only(m.from_user.id):
        return

    state = await _get_fsm(m.from_user.id)
    if not state:
        return

    text = (m.text or "").strip()

    # Direct message
    if state.startswith("a_msgtext:"):
        try:
            uid = int(state.split(":", 1)[1])
            await bot.send_message(
                uid,
                m.text,
                parse_mode="HTML",
            )

            async with Session() as s:
                await audit(
                    s,
                    m.from_user.id,
                    "DIRECT_USER_MESSAGE",
                    "user",
                    str(uid),
                    {"text": m.text[:500]},
                )
                await s.commit()

            await m.answer(
                f"✅ Xabar <code>{uid}</code> ID ga yuborildi.",
                parse_mode="HTML",
            )
        except Exception as e:
            await m.answer(
                f"❌ Yuborib bo‘lmadi: <code>{type(e).__name__}</code>",
                parse_mode="HTML",
            )

        await _delete_fsm(m.from_user.id)
        return

    # Order cost
    if state.startswith("a_cost:"):
        try:
            oid = int(state.split(":", 1)[1])
            cost = Decimal(text)
            if cost < 0:
                raise ValueError

            async with Session() as s:
                o = await s.get(Order, oid)
                if not o:
                    await m.answer("❌ Buyurtma topilmadi.")
                    await _delete_fsm(m.from_user.id)
                    return

                o.cost_amount = cost
                o.net_profit = Decimal(o.amount) - cost

                s.add(
                    CostLedger(
                        order_id=oid,
                        kind="ORDER_COST",
                        amount=cost,
                        note="Admin entered cost",
                    )
                )

                await audit(
                    s,
                    m.from_user.id,
                    "ORDER_COST_SET",
                    "order",
                    o.public_id,
                    {"cost": str(cost)},
                )
                await s.commit()

                net_profit = o.net_profit

            await m.answer(
                f"✅ Tannarx saqlandi: <b>{som(cost)}</b>\n"
                f"Net profit: <b>{som(net_profit)}</b>",
                parse_mode="HTML",
            )
        except Exception as e:
            await m.answer(
                f"❌ Tannarx noto‘g‘ri: <code>{type(e).__name__}</code>",
                parse_mode="HTML",
            )

        await _delete_fsm(m.from_user.id)
        return

    # User ID for direct message
    if state == "a_msgid":
        try:
            uid = int(text)
            if uid <= 0:
                raise ValueError

            await redis.set(
                f"fsm:{m.from_user.id}",
                f"a_msgtext:{uid}",
                ex=900,
            )

            await m.answer(
                f"<b>📨 {uid}</b>\n\n"
                "Endi HTML xabarni yuboring.",
                parse_mode="HTML",
            )
        except ValueError:
            await m.answer("❌ ID raqam bo‘lishi kerak.")
        return

    # Stars price
    if state == "a_price":
        try:
            new_price = Decimal(text)
            if new_price <= 0:
                raise ValueError

            async with Session() as s:
                old = await get_decimal(
                    "stars_unit_price",
                    Decimal(settings.stars_unit_price),
                )
                await set_value(
                    "stars_unit_price",
                    str(new_price),
                )
                s.add(
                    PriceHistory(
                        key="stars_unit_price",
                        old_value=str(old),
                        new_value=str(new_price),
                        admin_id=m.from_user.id,
                    )
                )
                await audit(
                    s,
                    m.from_user.id,
                    "PRICE_CHANGED",
                    "setting",
                    "stars_unit_price",
                    {
                        "old": str(old),
                        "new": str(new_price),
                    },
                )
                await s.commit()

            await m.answer(
                f"✅ 1 Stars = <b>{som(new_price)}</b>",
                parse_mode="HTML",
            )
        except Exception as e:
            await m.answer(
                f"❌ Narx noto‘g‘ri: <code>{type(e).__name__}</code>",
                parse_mode="HTML",
            )

        await _delete_fsm(m.from_user.id)
        return

    # Stars package
    if state == "a_pack":
        try:
            n = int(text)
            if n <= 0:
                raise ValueError

            async with Session() as s:
                s.add(
                    StarPackage(
                        stars=n,
                        active=True,
                    )
                )
                await s.commit()

            await m.answer(
                f"✅ {n} Stars paket qo‘shildi."
            )
        except Exception as e:
            await m.answer(
                f"❌ Paket noto‘g‘ri: <code>{type(e).__name__}</code>",
                parse_mode="HTML",
            )

        await _delete_fsm(m.from_user.id)
        return

    # Gift add
    if state == "a_giftadd":
        try:
            telegram_gift_id, name, stars_raw = text.split("|", 2)
            telegram_gift_id = telegram_gift_id.strip()
            name = name.strip()
            stars = int(stars_raw.strip())

            if not telegram_gift_id or not name or stars <= 0:
                raise ValueError

            async with Session() as s:
                s.add(
                    Gift(
                        telegram_gift_id=telegram_gift_id,
                        name=name,
                        stars=stars,
                        active=True,
                    )
                )
                await s.commit()

            await m.answer(
                "✅ Gift qo‘shildi."
            )
        except Exception as e:
            await m.answer(
                f"❌ Format noto‘g‘ri: <code>{type(e).__name__}</code>",
                parse_mode="HTML",
            )

        await _delete_fsm(m.from_user.id)
        return

    # Contest
    if state == "a_contest":
        try:
            title, description, prize, hours_raw, winners_raw = text.split("|", 4)
            title = title.strip()
            description = description.strip()
            prize = prize.strip()
            hours = int(hours_raw.strip())
            winner_count = int(winners_raw.strip())

            if not title or hours <= 0 or winner_count <= 0:
                raise ValueError

            now = datetime.now(timezone.utc)

            async with Session() as s:
                s.add(
                    Contest(
                        title=title,
                        description=description,
                        prize=prize,
                        starts_at=now,
                        ends_at=now + timedelta(hours=hours),
                        winner_count=winner_count,
                        active=True,
                    )
                )
                await s.commit()

            await m.answer(
                "✅ Konkurs yaratildi."
            )
        except Exception as e:
            await m.answer(
                f"❌ Format noto‘g‘ri: <code>{type(e).__name__}</code>",
                parse_mode="HTML",
            )

        await _delete_fsm(m.from_user.id)
        return

    # Promo
    if state == "a_promo":
        try:
            code, kind, value_raw, maxuses_raw, minorder_raw = text.split("|", 4)
            code = code.strip().upper()
            kind = kind.strip().upper()
            value = Decimal(value_raw.strip())
            max_uses = int(maxuses_raw.strip())
            min_order = Decimal(minorder_raw.strip())

            if not code or kind not in {"FIXED", "PERCENT"}:
                raise ValueError
            if value <= 0 or max_uses < 0 or min_order < 0:
                raise ValueError

            async with Session() as s:
                s.add(
                    PromoCode(
                        code=code,
                        kind=kind,
                        value=value,
                        max_uses=max_uses,
                        min_order=min_order,
                        active=True,
                    )
                )
                await s.commit()

            await m.answer(
                "✅ Promo-kod yaratildi."
            )
        except Exception as e:
            await m.answer(
                f"❌ Format noto‘g‘ri: <code>{type(e).__name__}</code>",
                parse_mode="HTML",
            )

        await _delete_fsm(m.from_user.id)
        return

    # Cashback
    if state == "a_cashback":
        try:
            value = Decimal(text)
            if value < 0 or value > 100:
                raise ValueError

            async with Session() as s:
                await set_value(
                    "cashback_percent",
                    str(value),
                )
                await audit(
                    s,
                    m.from_user.id,
                    "CASHBACK_CHANGED",
                    "setting",
                    "cashback_percent",
                    {"new": str(value)},
                )
                await s.commit()

            await m.answer(
                f"✅ Cashback {value}% bo‘ldi."
            )
        except Exception as e:
            await m.answer(
                f"❌ Foiz noto‘g‘ri: <code>{type(e).__name__}</code>",
                parse_mode="HTML",
            )

        await _delete_fsm(m.from_user.id)
        return

    # Referral bonus
    if state == "a_referral":
        try:
            value = Decimal(text)
            if value < 0:
                raise ValueError

            async with Session() as s:
                await set_value(
                    "referral_bonus",
                    str(value),
                )
                await audit(
                    s,
                    m.from_user.id,
                    "REFERRAL_BONUS_CHANGED",
                    "setting",
                    "referral_bonus",
                    {"new": str(value)},
                )
                await s.commit()

            await m.answer(
                f"✅ Referral bonus {som(value)} bo‘ldi."
            )
        except Exception as e:
            await m.answer(
                f"❌ Summa noto‘g‘ri: <code>{type(e).__name__}</code>",
                parse_mode="HTML",
            )

        await _delete_fsm(m.from_user.id)
        return

    # CRM
    if state == "a_crm":
        q = text.lstrip("@").strip()
        user = None

        if not q:
            await m.answer("❌ Telegram ID yoki username yuboring.")
            return

        async with Session() as s:
            try:
                user = await s.get(User, int(q))
            except ValueError:
                user = await s.scalar(
                    select(User).where(
                        User.username.ilike(q)
                    )
                )

            if not user:
                await m.answer("❌ User topilmadi.")
            else:
                await m.answer(
                    "<b>👥 CRM</b>\n\n"
                    f"🆔 <code>{user.id}</code>\n"
                    f"👤 @{user.username or '—'}\n"
                    f"💰 {som(user.balance)}\n"
                    f"🎁 Cashback: {som(user.cashback)}\n"
                    f"👥 Referrals: {user.referral_count}\n"
                    f"💵 Lifetime: {som(user.lifetime_spent)}\n"
                    f"🚦 {user.account_status}",
                    parse_mode="HTML",
                )

        await _delete_fsm(m.from_user.id)
        return

    # Broadcast
    if state == "a_broadcast":
        try:
            segment, broadcast_text = text.split("|", 1)
            segment = segment.strip().upper()
            broadcast_text = broadcast_text.strip()

            if segment not in {"ALL", "NEW", "BUYERS", "VIP"}:
                raise ValueError
            if not broadcast_text:
                raise ValueError

            async with Session() as s:
                b = Broadcast(
                    actor_id=m.from_user.id,
                    text=broadcast_text,
                    segment=segment,
                    status="QUEUED",
                )
                s.add(b)
                await s.flush()

                await redis.rpush(
                    "broadcast",
                    json.dumps(
                        {"id": b.id},
                        ensure_ascii=False,
                    ),
                )
                await s.commit()

                broadcast_id = b.id

            await m.answer(
                f"✅ Broadcast #{broadcast_id} navbatga qo‘yildi."
            )
        except Exception as e:
            await m.answer(
                "❌ Format: <code>SEGMENT|TEXT</code>\n"
                f"Xato: <code>{type(e).__name__}</code>",
                parse_mode="HTML",
            )

        await _delete_fsm(m.from_user.id)
        return

    # Unknown state
    await m.answer(
        "⚠️ Admin holati topilmadi. Admin paneldan operatsiyani qayta boshlang."
    )
    await _delete_fsm(m.from_user.id)
