from __future__ import annotations

import asyncio
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import settings


async def run_migrations() -> None:
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    async with engine.connect() as conn:
        await conn.execute(text("SELECT pg_advisory_lock(4862017)"))
        try:
            proc = await asyncio.create_subprocess_exec("alembic", "upgrade", "head")
            code = await proc.wait()
            if code != 0:
                raise SystemExit(code)
        finally:
            await conn.execute(text("SELECT pg_advisory_unlock(4862017)"))
    await engine.dispose()


async def main(mode: str = "web") -> None:
    await run_migrations()

    if mode == "web":
        import uvicorn

        config = uvicorn.Config(
            "app.web:app",
            host="0.0.0.0",
            port=settings.port,
            log_level="info",
        )
        server = uvicorn.Server(config)
        await server.serve()
        return

    if mode == "worker":
        from app.worker import run
        await run()
        return

    raise ValueError(f"Unknown mode: {mode}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "web"))
