# db/connection.py
import os, psycopg
from psycopg.rows import dict_row

async def get_pool():
    return await psycopg.AsyncConnection.connect(os.getenv("DATABASE_URL"), row_factory=dict_row)