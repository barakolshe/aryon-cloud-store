from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import settings

app = FastAPI()

engine = create_async_engine(settings.database_url)


@app.get('/tenants')
async def get_tenants():
    async with engine.connect() as conn:
        result = await conn.execute(text('SELECT tenant_id, tenant_name FROM tenants'))
        return [dict(row._mapping) for row in result]
