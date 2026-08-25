from pydantic_settings import BaseSettings, SettingsConfigDict
class Settings(BaseSettings):
    model_config=SettingsConfigDict(env_file='.env',env_file_encoding='utf-8',extra='ignore')
    bot_token:str
    database_url:str
    redis_url:str
    admin_ids:str=''
    log_chat_id:int|None=None
    public_feed_chat_id:int|None=None
    public_feed_enabled:bool=False
    mandatory_channels:str=''
    payment_card:str=''
    payment_card_holder:str=''
    min_topup:int=1000
    max_topup:int=1_000_000
    stars_unit_price:int=200
    cashback_percent:float=2.0
    referral_bonus:int=500
    bot_name:str='⭐️ Stars & Gifts Shop'
    bot_username:str=''
    public_url:str=''
    webhook_secret:str='change-me'
    port:int=10000
    ocr_enabled:bool=False
    animation_stars_file_id:str=''
    animation_gifts_file_id:str=''
    animation_topup_file_id:str=''
    animation_contest_file_id:str=''
    receipt_logo_url:str=''
    @property
    def admins(self)->set[int]: return {int(x.strip()) for x in self.admin_ids.split(',') if x.strip()}
    @property
    def channels(self)->list[str]: return [x.strip() for x in self.mandatory_channels.split(',') if x.strip()]
settings=Settings()
if settings.database_url.startswith('postgres://'):
    settings.database_url=settings.database_url.replace('postgres://','postgresql+asyncpg://',1)
elif settings.database_url.startswith('postgresql://'):
    settings.database_url=settings.database_url.replace('postgresql://','postgresql+asyncpg://',1)
