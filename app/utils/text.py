from decimal import Decimal

def som(x): return f"{Decimal(str(x)):,.0f}".replace(',',' ')+' so‘m'

def home_text(user,bot_name):
    return (f'<b>✨ {bot_name}</b>\n\n<b>Eng tezkor va ishonchli xizmatlardan foydalaning</b>\n\n'
            f'🆔 ID: <code>{user.id}</code>\n👤 @{user.username or "username yo‘q"}\n'
            f'💰 Balans: <b>{som(user.balance)}</b>\n🎁 Cashback: <b>{som(user.cashback)}</b>\n'
            f'👥 Referral: <b>{user.referral_count}</b>\n📦 Umumiy xarid: <b>{som(user.lifetime_spent)}</b>\n\n'
            '👇 <b>Kerakli xizmatni tanlang:</b>')
