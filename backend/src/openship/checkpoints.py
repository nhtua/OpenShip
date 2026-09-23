import aiosqlite
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver


async def get_checkpointer() -> AsyncSqliteSaver:
    conn = await aiosqlite.connect("checkpoints.db")
    return AsyncSqliteSaver(conn)
