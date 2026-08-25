from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

def kb(rows):
    b=InlineKeyboardBuilder()
    for row in rows:
        b.row(*[InlineKeyboardButton(text=t,callback_data=c) for t,c in row],width=max(1,min(len(row),3)))
    return b.as_markup()

def main_menu(is_admin=False):
    rows=[
        [('🏆 Konkursda ishtirok etish','contest'),('⭐️ Stars olish','stars')],
        [('🎁 Gift olish','gifts'),('💳 Hisob to‘ldirish','topup')],
        [('👤 Profilim','profile'),('💬 Adminga habar','support')],
        [('🎟 Promo-kod','promo'),('🔔 Xabarnomalar','notifications')]
    ]
    if is_admin: rows.append([('🛠 Admin panel','admin')])
    return kb(rows)

def back(data='home'): return kb([[('⬅️ Orqaga',data)]])

def confirm(callback='confirmdraft',cancel='home'): return kb([[('✅ Tasdiqlash',callback),('❌ Bekor qilish',cancel)]])
