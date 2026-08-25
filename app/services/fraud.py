import hashlib,re
from decimal import Decimal

def sha256_bytes(data:bytes)->str:return hashlib.sha256(data).hexdigest()
def assess_receipt(text:str,expected:Decimal,duplicate:bool=False):
    score=0; reasons=[]; t=(text or '').lower()
    if duplicate: score+=80; reasons.append('Duplicate receipt')
    nums=re.findall(r'\d+[\d\s,.]*',t)
    cleaned=' '.join(nums)
    exp=int(Decimal(expected))
    if str(exp) not in cleaned.replace(' ','').replace(',','').replace('.',''):
        score+=35; reasons.append('OCR summa mos kelmadi')
    if not t: score+=20; reasons.append('OCR matn topilmadi')
    status='LOW' if score<30 else 'MEDIUM' if score<60 else 'HIGH'
    return score,status,reasons
