from __future__ import annotations
from datetime import datetime
from decimal import Decimal
import enum
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import BigInteger, String, Integer, Boolean, DateTime, Text, Numeric, ForeignKey, UniqueConstraint, Index, func
from app.config import settings

class Base(DeclarativeBase):
    pass

class OrderStatus(str, enum.Enum):
    PENDING='PENDING'; PROCESSING='PROCESSING'; COMPLETED='COMPLETED'; FAILED='FAILED'; CANCELLED='CANCELLED'; REFUNDED='REFUNDED'
class OrderType(str, enum.Enum): STARS='STARS'; GIFT='GIFT'
class PaymentStatus(str, enum.Enum): PENDING='PENDING'; APPROVED='APPROVED'; REJECTED='REJECTED'

class User(Base):
    __tablename__='users'
    id: Mapped[int]=mapped_column(BigInteger, primary_key=True)
    username: Mapped[str|None]=mapped_column(String(255), index=True)
    first_name: Mapped[str|None]=mapped_column(String(255))
    last_name: Mapped[str|None]=mapped_column(String(255))
    balance: Mapped[Decimal]=mapped_column(Numeric(18,2), default=0)
    cashback: Mapped[Decimal]=mapped_column(Numeric(18,2), default=0)
    referral_count: Mapped[int]=mapped_column(Integer, default=0)
    referred_by: Mapped[int|None]=mapped_column(BigInteger, ForeignKey('users.id', ondelete='SET NULL'), index=True)
    is_blocked: Mapped[bool]=mapped_column(Boolean, default=False, index=True)
    account_status: Mapped[str]=mapped_column(String(30), default='ACTIVE', index=True)
    lifetime_spent: Mapped[Decimal]=mapped_column(Numeric(18,2), default=0)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True), server_default=func.now())
    last_active_at: Mapped[datetime]=mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

class Setting(Base):
    __tablename__='settings'
    key: Mapped[str]=mapped_column(String(120), primary_key=True)
    value: Mapped[str]=mapped_column(Text)
    updated_at: Mapped[datetime]=mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

class StarPackage(Base):
    __tablename__='star_packages'
    id: Mapped[int]=mapped_column(Integer, primary_key=True)
    stars: Mapped[int]=mapped_column(Integer, unique=True)
    active: Mapped[bool]=mapped_column(Boolean, default=True)

class Gift(Base):
    __tablename__='gifts'
    id: Mapped[int]=mapped_column(Integer, primary_key=True)
    telegram_gift_id: Mapped[str]=mapped_column(String(255), unique=True)
    name: Mapped[str]=mapped_column(String(255))
    stars: Mapped[int]=mapped_column(Integer)
    active: Mapped[bool]=mapped_column(Boolean, default=True)
    available: Mapped[int|None]=mapped_column(Integer, nullable=True)
    image_file_id: Mapped[str|None]=mapped_column(String(255))
    updated_at: Mapped[datetime]=mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

class Order(Base):
    __tablename__='orders'
    id: Mapped[int]=mapped_column(Integer, primary_key=True)
    public_id: Mapped[str]=mapped_column(String(40), unique=True, index=True)
    user_id: Mapped[int]=mapped_column(BigInteger, ForeignKey('users.id'), index=True)
    order_type: Mapped[str]=mapped_column(String(20), index=True)
    status: Mapped[str]=mapped_column(String(20), default='PENDING', index=True)
    quantity: Mapped[int]=mapped_column(Integer)
    target_username: Mapped[str]=mapped_column(String(255))
    item_name: Mapped[str|None]=mapped_column(String(255))
    amount: Mapped[Decimal]=mapped_column(Numeric(18,2))
    unit_price: Mapped[Decimal]=mapped_column(Numeric(18,2))
    discount: Mapped[Decimal]=mapped_column(Numeric(18,2), default=0)
    cost_amount: Mapped[Decimal]=mapped_column(Numeric(18,2), default=0)
    net_profit: Mapped[Decimal]=mapped_column(Numeric(18,2), default=0)
    cashback_awarded: Mapped[Decimal]=mapped_column(Numeric(18,2), default=0)
    promo_code: Mapped[str|None]=mapped_column(String(64))
    comment: Mapped[str|None]=mapped_column(Text)
    gift_mode: Mapped[str|None]=mapped_column(String(20))
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime]=mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    completed_at: Mapped[datetime|None]=mapped_column(DateTime(timezone=True))
    completed_by: Mapped[int|None]=mapped_column(BigInteger)

class OrderEvent(Base):
    __tablename__='order_events'
    id: Mapped[int]=mapped_column(Integer, primary_key=True)
    order_id: Mapped[int]=mapped_column(Integer, ForeignKey('orders.id', ondelete='CASCADE'), index=True)
    actor_id: Mapped[int|None]=mapped_column(BigInteger)
    event: Mapped[str]=mapped_column(String(80), index=True)
    details: Mapped[str|None]=mapped_column(Text)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True), server_default=func.now())

class BalanceLedger(Base):
    __tablename__='balance_ledger'
    id: Mapped[int]=mapped_column(Integer, primary_key=True)
    user_id: Mapped[int]=mapped_column(BigInteger, ForeignKey('users.id'), index=True)
    kind: Mapped[str]=mapped_column(String(60), index=True)
    amount: Mapped[Decimal]=mapped_column(Numeric(18,2))
    balance_after: Mapped[Decimal]=mapped_column(Numeric(18,2))
    reference: Mapped[str|None]=mapped_column(String(120), index=True)
    note: Mapped[str|None]=mapped_column(Text)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True), server_default=func.now())

class CashbackLedger(Base):
    __tablename__='cashback_ledger'
    id: Mapped[int]=mapped_column(Integer, primary_key=True)
    user_id: Mapped[int]=mapped_column(BigInteger, ForeignKey('users.id'), index=True)
    kind: Mapped[str]=mapped_column(String(50), index=True)
    amount: Mapped[Decimal]=mapped_column(Numeric(18,2))
    balance_after: Mapped[Decimal]=mapped_column(Numeric(18,2))
    reference: Mapped[str|None]=mapped_column(String(120), index=True)
    note: Mapped[str|None]=mapped_column(Text)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True), server_default=func.now())

class Payment(Base):
    __tablename__='payments'
    id: Mapped[int]=mapped_column(Integer, primary_key=True)
    public_id: Mapped[str]=mapped_column(String(40), unique=True, index=True)
    user_id: Mapped[int]=mapped_column(BigInteger, ForeignKey('users.id'), index=True)
    amount: Mapped[Decimal]=mapped_column(Numeric(18,2))
    status: Mapped[str]=mapped_column(String(20), default='PENDING', index=True)
    receipt_file_id: Mapped[str|None]=mapped_column(String(255))
    receipt_hash: Mapped[str|None]=mapped_column(String(128), index=True)
    ocr_amount: Mapped[Decimal|None]=mapped_column(Numeric(18,2))
    ocr_text: Mapped[str|None]=mapped_column(Text)
    risk_score: Mapped[int]=mapped_column(Integer, default=0)
    risk_status: Mapped[str]=mapped_column(String(20), default='LOW')
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True), server_default=func.now())
    approved_at: Mapped[datetime|None]=mapped_column(DateTime(timezone=True))
    approved_by: Mapped[int|None]=mapped_column(BigInteger)

class PromoCode(Base):
    __tablename__='promo_codes'
    id: Mapped[int]=mapped_column(Integer, primary_key=True)
    code: Mapped[str]=mapped_column(String(64), unique=True, index=True)
    kind: Mapped[str]=mapped_column(String(20))
    value: Mapped[Decimal]=mapped_column(Numeric(18,2))
    max_uses: Mapped[int|None]=mapped_column(Integer)
    used_count: Mapped[int]=mapped_column(Integer, default=0)
    min_order: Mapped[Decimal]=mapped_column(Numeric(18,2), default=0)
    active: Mapped[bool]=mapped_column(Boolean, default=True)
    expires_at: Mapped[datetime|None]=mapped_column(DateTime(timezone=True))

class PromoUse(Base):
    __tablename__='promo_uses'
    id: Mapped[int]=mapped_column(Integer, primary_key=True)
    promo_id: Mapped[int]=mapped_column(Integer, ForeignKey('promo_codes.id', ondelete='CASCADE'))
    user_id: Mapped[int]=mapped_column(BigInteger, ForeignKey('users.id', ondelete='CASCADE'))
    order_id: Mapped[int|None]=mapped_column(Integer, ForeignKey('orders.id', ondelete='SET NULL'))
    used_at: Mapped[datetime]=mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__=(UniqueConstraint('promo_id','user_id',name='uq_promo_user'),)

class Contest(Base):
    __tablename__='contests'
    id: Mapped[int]=mapped_column(Integer, primary_key=True)
    title: Mapped[str]=mapped_column(String(255))
    description: Mapped[str]=mapped_column(Text)
    prize: Mapped[str]=mapped_column(String(255))
    image_file_id: Mapped[str|None]=mapped_column(String(255))
    starts_at: Mapped[datetime]=mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime]=mapped_column(DateTime(timezone=True))
    winner_count: Mapped[int]=mapped_column(Integer, default=1)
    active: Mapped[bool]=mapped_column(Boolean, default=True)
    finished: Mapped[bool]=mapped_column(Boolean, default=False)

class ContestParticipant(Base):
    __tablename__='contest_participants'
    id: Mapped[int]=mapped_column(Integer, primary_key=True)
    contest_id: Mapped[int]=mapped_column(Integer, ForeignKey('contests.id',ondelete='CASCADE'))
    user_id: Mapped[int]=mapped_column(BigInteger, ForeignKey('users.id',ondelete='CASCADE'))
    referral_count: Mapped[int]=mapped_column(Integer, default=0)
    qualified: Mapped[bool]=mapped_column(Boolean, default=True)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__=(UniqueConstraint('contest_id','user_id',name='uq_contest_user'), Index('ix_contest_rank','contest_id','referral_count'))

class SupportTicket(Base):
    __tablename__='support_tickets'
    id: Mapped[int]=mapped_column(Integer, primary_key=True)
    public_id: Mapped[str]=mapped_column(String(40), unique=True, index=True)
    user_id: Mapped[int]=mapped_column(BigInteger, ForeignKey('users.id'))
    message: Mapped[str]=mapped_column(Text)
    status: Mapped[str]=mapped_column(String(20), default='OPEN')
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True), server_default=func.now())
    closed_at: Mapped[datetime|None]=mapped_column(DateTime(timezone=True))

class TicketMessage(Base):
    __tablename__='ticket_messages'
    id: Mapped[int]=mapped_column(Integer, primary_key=True)
    ticket_id: Mapped[int]=mapped_column(Integer, ForeignKey('support_tickets.id',ondelete='CASCADE'), index=True)
    actor_id: Mapped[int]=mapped_column(BigInteger)
    message: Mapped[str]=mapped_column(Text)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True), server_default=func.now())

class CostLedger(Base):
    __tablename__='cost_ledger'
    id: Mapped[int]=mapped_column(Integer, primary_key=True)
    order_id: Mapped[int|None]=mapped_column(Integer, ForeignKey('orders.id',ondelete='SET NULL'), index=True)
    kind: Mapped[str]=mapped_column(String(50), index=True)
    amount: Mapped[Decimal]=mapped_column(Numeric(18,2))
    note: Mapped[str|None]=mapped_column(Text)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True), server_default=func.now())

class Notification(Base):
    __tablename__='notifications'
    id: Mapped[int]=mapped_column(Integer, primary_key=True)
    user_id: Mapped[int]=mapped_column(BigInteger, ForeignKey('users.id'), index=True)
    kind: Mapped[str]=mapped_column(String(50))
    title: Mapped[str]=mapped_column(String(255))
    body: Mapped[str]=mapped_column(Text)
    is_read: Mapped[bool]=mapped_column(Boolean, default=False)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True), server_default=func.now())

class Broadcast(Base):
    __tablename__='broadcasts'
    id: Mapped[int]=mapped_column(Integer, primary_key=True)
    actor_id: Mapped[int]=mapped_column(BigInteger)
    text: Mapped[str]=mapped_column(Text)
    segment: Mapped[str]=mapped_column(String(40), default='ALL')
    status: Mapped[str]=mapped_column(String(20), default='QUEUED')
    sent_count: Mapped[int]=mapped_column(Integer, default=0)
    failed_count: Mapped[int]=mapped_column(Integer, default=0)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True), server_default=func.now())

class SystemError(Base):
    __tablename__='system_errors'
    id: Mapped[int]=mapped_column(Integer, primary_key=True)
    public_id: Mapped[str]=mapped_column(String(40), unique=True, index=True)
    module: Mapped[str]=mapped_column(String(100))
    message: Mapped[str]=mapped_column(Text)
    resolved: Mapped[bool]=mapped_column(Boolean, default=False)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True), server_default=func.now())

class PriceHistory(Base):
    __tablename__='price_history'
    id: Mapped[int]=mapped_column(Integer, primary_key=True)
    key: Mapped[str]=mapped_column(String(100), index=True)
    old_value: Mapped[str|None]=mapped_column(String(100))
    new_value: Mapped[str]=mapped_column(String(100))
    admin_id: Mapped[int]=mapped_column(BigInteger)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True), server_default=func.now())

class AuditLog(Base):
    __tablename__='audit_logs'
    id: Mapped[int]=mapped_column(Integer, primary_key=True)
    actor_id: Mapped[int|None]=mapped_column(BigInteger, index=True)
    action: Mapped[str]=mapped_column(String(120), index=True)
    entity: Mapped[str|None]=mapped_column(String(100))
    entity_id: Mapped[str|None]=mapped_column(String(100))
    details: Mapped[str|None]=mapped_column(Text)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True), server_default=func.now())

engine=create_async_engine(settings.database_url, pool_pre_ping=True, pool_size=10, max_overflow=20)
Session=async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
