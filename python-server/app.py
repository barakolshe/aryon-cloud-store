import os

from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

app = FastAPI()

# Read inline for now; this moves into app/core/config.py with the central config issue.
# No fallback on purpose -- a missing DATABASE_URL should fail loudly at startup.
database_url = os.environ['DATABASE_URL'].replace('postgresql://', 'postgresql+psycopg://', 1)
engine = create_async_engine(database_url)


@app.get('/tenants')
async def get_tenants():
    async with engine.connect() as conn:
        result = await conn.execute(text('SELECT tenant_id, tenant_name FROM tenants'))
        return [dict(row._mapping) for row in result]
