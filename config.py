# config.py

import os
from dotenv import load_dotenv

# --- 核心步骤: 加载 .env 文件 ---
load_dotenv()

# --- 全局配置变量 ---
try:
    TOKEN = os.environ['TOKEN']
    ADMIN_GROUP_ID = int(os.environ['ADMIN_GROUP_ID'])
    CHANNEL_ID = os.environ['CHANNEL_ID']
    CHANNEL_USERNAME = os.environ['CHANNEL_USERNAME']
    DISCUSSION_GROUP_ID = int(os.environ['DISCUSSION_GROUP_ID'])
    BOT_USERNAME = os.environ['BOT_USERNAME']
    
    # 获取 Railway 的数据库连接字符串
    # 格式通常为: postgresql://user:password@host:port/database
    DATABASE_URL = os.environ['DATABASE_URL'] 

except KeyError as e:
    raise RuntimeError(f"错误: 关键环境变量 {e} 缺失！请检查 .env 文件或 Railway 变量设置。")


# --- 对话状态定义 ---
(
    CHOOSING, 
    GETTING_POST, 
    BROWSING_POSTS, 
    BROWSING_COLLECTIONS, 
    COMMENTING,
    DELETING_COMMENT
) = range(6)
