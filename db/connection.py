import os, asyncpg
_pool = None

async def get_pool():
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(os.getenv("DATABASE_URL"), min_size=1, max_size=5)
    return _pool