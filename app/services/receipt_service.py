from io import BytesIO
from PIL import Image, ImageDraw, ImageFont
from aiogram.types import BufferedInputFile
from app.utils.text import som

def make_receipt(order,user,bot_name):
    img=Image.new('RGB',(1000,1300),'white'); d=ImageDraw.Draw(img)
    try:
        font=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',30)
        bold=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',42)
        small=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',24)
    except Exception:
        font=bold=small=None
    d.text((70,70),bot_name,font=bold,fill='black'); d.text((70,145),'ELEKTRON CHEK',font=font,fill='black')
    y=240
    rows=[('Buyurtma ID',order.public_id),('Sana',str(order.completed_at or order.created_at)),('Username',f'@{user.username or "—"}'),('Target',order.target_username),('Xizmat',order.item_name or order.order_type),('Miqdor',str(order.quantity)),('To‘langan summa',som(order.amount)),('Cashback',som(order.cashback_awarded))]
    for k,v in rows:
        d.text((70,y),k,font=small,fill='black'); d.text((430,y),str(v),font=font,fill='black'); y+=105
    d.line((70,y,930,y),fill='black',width=2); y+=60; d.text((70,y),'✅ BUYURTMA BAJARILDI',font=bold,fill='black')
    bio=BytesIO(); img.save(bio,format='PNG'); return BufferedInputFile(bio.getvalue(),filename=f'{order.public_id}.png')
