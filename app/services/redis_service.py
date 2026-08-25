from redis.asyncio import Redis
from app.config import settings
import json
redis=Redis.from_url(settings.redis_url,decode_responses=True)
async def rate_limit(key,limit=8,window=10):
    k=f'rl:{key}'; count=await redis.incr(k)
    if count==1: await redis.expire(k,window)
    return count<=limit
async def state_set(uid,key,value,ex=900): await redis.set(f'fsm:{uid}:{key}',json.dumps(value,ensure_ascii=False),ex=ex)
async def state_get(uid,key):
    v=await redis.get(f'fsm:{uid}:{key}')
    return json.loads(v) if v else None
async def state_del(uid,key): await redis.delete(f'fsm:{uid}:{key}')
async def push_job(kind,payload): await redis.rpush('bot:jobs',json.dumps({'kind':kind,'payload':payload},ensure_ascii=False))
async def pop_job(timeout=5):
    x=await redis.blpop('bot:jobs',timeout=timeout)
    return json.loads(x[1]) if x else None
