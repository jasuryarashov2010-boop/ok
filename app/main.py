# Compatibility entry point.
from app.worker import run
import asyncio
if __name__=='__main__':asyncio.run(run())
