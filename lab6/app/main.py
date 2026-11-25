from contextlib import asynccontextmanager
from fastapi import FastAPI
from strawberry.fastapi import GraphQLRouter
from . import db
from .schema import schema, context_getter

@asynccontextmanager
async def lifespan(app):
    async with db.engine.begin() as connection:
        await connection.run_sync(db.Base.metadata.create_all)
    yield
    await db.engine.dispose()

app = FastAPI(lifespan=lifespan)
app.include_router(GraphQLRouter(schema, context_getter=context_getter), prefix='/graphql')
