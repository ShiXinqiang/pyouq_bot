# test_delete.py - 测试删除评论功能 (PostgreSQL版)

import asyncio
import asyncpg
import os
from dotenv import load_dotenv

load_dotenv()
DATABASE_URL = os.environ.get('DATABASE_URL')

async def test_comment_data(message_id: int, user_id: int):
    """测试评论数据"""
    print(f"\n=== 测试帖子 {message_id} 的评论 ===")
    print(f"用户ID: {user_id}")
    
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        # 检查帖子是否存在
        post_info = await conn.fetchrow(
            "SELECT user_id FROM submissions WHERE channel_message_id = $1",
            message_id
        )
        
        if not post_info:
            print("❌ 帖子不存在！")
            return
        
        author_id = post_info['user_id']
        is_author = (user_id == author_id)
        
        print(f"帖子作者ID: {author_id}")
        print(f"是否是作者: {is_author}")
        
        # 查询用户自己的评论
        my_comments = await conn.fetch(
            "SELECT id, comment_text FROM comments WHERE channel_message_id = $1 AND user_id = $2 ORDER BY timestamp DESC",
            message_id, user_id
        )
        
        print(f"\n📝 你的评论（{len(my_comments)}条）:")
        for idx, row in enumerate(my_comments, 1):
            print(f"  {idx}. ID={row['id']}, 内容: {row['comment_text'][:30]}...")
        
        # 如果是作者，查询其他人的评论
        if is_author:
            other_comments = await conn.fetch(
                "SELECT id, user_name, comment_text FROM comments WHERE channel_message_id = $1 AND user_id != $2 ORDER BY timestamp DESC",
                message_id, user_id
            )
            
            print(f"\n👥 其他人的评论（{len(other_comments)}条）:")
            start_num = len(my_comments) + 1
            for idx, row in enumerate(other_comments, start_num):
                print(f"  {idx}. ID={row['id']}, {row['user_name']}: {row['comment_text'][:30]}...")
                
    finally:
        await conn.close()

# 使用方法：
# python test_delete.py
# 然后输入帖子ID和用户ID

if __name__ == "__main__":
    if not DATABASE_URL:
        print("错误: 环境变量 DATABASE_URL 未设置")
        exit(1)
        
    try:
        message_id = int(input("输入帖子ID（channel_message_id）: "))
        user_id = int(input("输入你的用户ID: "))
        asyncio.run(test_comment_data(message_id, user_id))
    except ValueError:
        print("请输入有效的数字ID")
