import importlib
import re
from typing import List

from sqlmodel import SQLModel
from sqlalchemy.sql import text

from rover.logger import logger

from . import base_models
from .base_models import init_database

CORE_DATABASE_MODEL_MODULES = ("rover.utils.database.models",)

# 插件在导入时 extend 本列表, 因此必须是模块级可变 list
exec_list: List[str] = [
    "ALTER TABLE Subscribe ADD COLUMN uid TEXT DEFAULT NULL;",
    "ALTER TABLE Subscribe ADD COLUMN WS_BOT_ID TEXT DEFAULT NULL;",
    "ALTER TABLE Subscribe ADD COLUMN extra_data TEXT DEFAULT NULL;",
    "ALTER TABLE Subscribe ADD COLUMN msg_id TEXT DEFAULT NULL;",
    "CREATE INDEX ix_subscribe_task_name ON Subscribe (task_name);",
    "CREATE INDEX ix_subscribe_uid ON Subscribe (uid);",
    "CREATE INDEX ix_subscribe_task_name_uid ON Subscribe (task_name, uid);",
]


def import_database_models() -> None:
    """导入表模型, 确保 create_all 能看到完整 metadata。"""
    for module_name in CORE_DATABASE_MODEL_MODULES:
        try:
            importlib.import_module(module_name)
        except Exception as e:
            raise RuntimeError(f'database model import failed: {module_name}') from e


async def create_core_tables() -> None:
    import_database_models()
    async with base_models.engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    logger.info("[数据库] 核心数据库表创建成功!")


async def trans_adapter() -> None:
    async with base_models.async_maker() as session:
        for position, _t in enumerate(exec_list):
            try:
                if await _migration_present(session, _t):
                    continue
                await session.execute(text(_t))
                await session.commit()
            except Exception as primary:
                try:
                    await session.rollback()
                except Exception as rollback_error:
                    primary._database_rollback_error = rollback_error
                primary.add_note(f'database migration {position} incomplete; startup must not report ready')
                raise


async def _migration_present(session, statement):
    """Skip only schema-proven completed DDL, never arbitrary errors.

    Legacy upstream lists replay on startup. A column already removed is the
    completed state of DROP COLUMN, just as an existing column completes ADD.
    get_columns must still succeed: a missing table or unreadable schema is
    not evidence that the migration completed.
    """
    from sqlalchemy import inspect
    column = re.fullmatch(r'\s*ALTER\s+TABLE\s+(\w+)\s+ADD\s+COLUMN\s+(\w+)\s+.+;?\s*', statement, re.I)
    index = re.fullmatch(r'\s*CREATE\s+INDEX\s+(\w+)\s+ON\s+(\w+)\s*\([^;]+\);?\s*', statement, re.I)
    removed = re.fullmatch(r'\s*ALTER\s+TABLE\s+(\w+)\s+DROP\s+COLUMN\s+(\w+)\s*;?\s*', statement, re.I)
    if column:
        table, name = column.groups()
        return await session.run_sync(lambda s: any(row['name'] == name for row in inspect(s.connection()).get_columns(table)))
    if index:
        name, table = index.groups()
        return await session.run_sync(lambda s: any(row['name'] == name for row in inspect(s.connection()).get_indexes(table)))
    if removed:
        table, name = removed.groups()
        return await session.run_sync(lambda s: not any(row['name'] == name for row in inspect(s.connection()).get_columns(table)))
    return False


async def init_all() -> None:
    """建库建表并补齐历史列。

    插件模块在导入时会绑定 base_models.engine 并 extend exec_list,
    因此需要先 await init_database() 再导入插件, 最后调用本函数。
    """
    await init_database()
    await create_core_tables()
    await trans_adapter()
