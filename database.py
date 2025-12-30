# database.py

import asyncpg
import logging
from telegram.ext import Application
from config import DATABASE_URL

logger = logging.getLogger(__name__)

_pool = None

async def get_pool():
    global _pool
    if _pool is None:
        try:
            _pool = await asyncpg.create_pool(dsn=DATABASE_URL)
            logger.info("✅ PostgreSQL 连接池已创建")
        except Exception as e:
            logger.error(f"❌ 无法连接到数据库: {e}")
            raise e
    return _pool

async def close_pool():
    global _pool
    if _pool:
        await _pool.close()
        logger.info("🛑 PostgreSQL 连接池已关闭")

async def setup_database(application: Application) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        # 增加 parent_id 字段
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS comments (
                id SERIAL PRIMARY KEY,
                channel_message_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                user_name TEXT NOT NULL,
                comment_text TEXT NOT NULL,
                parent_id BIGINT,  -- 新增：父评论ID
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # 其他表保持不变...
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS submissions (
                id SERIAL PRIMARY KEY, 
                user_id BIGINT NOT NULL,
                user_name TEXT, 
                channel_message_id BIGINT NOT NULL UNIQUE,
                content_text TEXT, 
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS reactions (
                id SERIAL PRIMARY KEY, 
                channel_message_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL, 
                reaction_type INTEGER NOT NULL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(channel_message_id, user_id)
            )
        ''')
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS collections (
                id SERIAL PRIMARY KEY, 
                channel_message_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL, 
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(channel_message_id, user_id)
            )
        ''')
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS pinned_posts (
                id SERIAL PRIMARY KEY,
                channel_message_id BIGINT NOT NULL UNIQUE,
                pinned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                like_count_at_pin INTEGER
            )
        ''')
        
        logger.info("数据库结构初始化完成 (V10.7 楼中楼版)。")
