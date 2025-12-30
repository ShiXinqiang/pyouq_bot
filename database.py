# database.py

import asyncpg
import logging
from config import DATABASE_URL

logger = logging.getLogger(__name__)

# 全局连接池变量
_pool = None

async def get_pool():
    """获取数据库连接池"""
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
    """关闭连接池"""
    global _pool
    if _pool:
        await _pool.close()
        logger.info("🛑 PostgreSQL 连接池已关闭")

async def setup_database(application) -> None:
    """创建或更新所有数据库表结构 (适配 PostgreSQL)"""
    pool = await get_pool()
    
    async with pool.acquire() as conn:
        # 主投稿表
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
        
        # 互动记录表
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
        
        # 收藏记录表
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS collections (
                id SERIAL PRIMARY KEY, 
                channel_message_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL, 
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(channel_message_id, user_id)
            )
        ''')
        
        # 评论表
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS comments (
                id SERIAL PRIMARY KEY,
                channel_message_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                user_name TEXT NOT NULL,
                comment_text TEXT NOT NULL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # 通知记录表
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS notifications (
                id SERIAL PRIMARY KEY,
                channel_message_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                notification_type TEXT NOT NULL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(channel_message_id, user_id, notification_type)
            )
        ''')
        
        # 置顶记录表
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS pinned_posts (
                id SERIAL PRIMARY KEY,
                channel_message_id BIGINT NOT NULL UNIQUE,
                pinned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                like_count_at_pin INTEGER
            )
        ''')
        
        logger.info("PostgreSQL 数据库表结构初始化完成。")
