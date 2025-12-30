# config.py

import os
from dotenv import load_dotenv

load_dotenv()

try:
    TOKEN = os.environ['TOKEN']
    ADMIN_GROUP_ID = int(os.environ['ADMIN_GROUP_ID'])
    CHANNEL_ID = os.environ['CHANNEL_ID']
    CHANNEL_USERNAME = os.environ['CHANNEL_USERNAME']
    DISCUSSION_GROUP_ID = int(os.environ['DISCUSSION_GROUP_ID'])
    BOT_USERNAME = os.environ['BOT_USERNAME']
    DATABASE_URL = os.environ['DATABASE_URL']
except KeyError as e:
    raise RuntimeError(f"错误: 关键环境变量 {e} 缺失！请检查 .env 文件。")

# --- 对话状态定义 ---
(
    CHOOSING, 
    GETTING_POST, 
    WAITING_CAPTION,      # <--- 新增：等待补发文案
    CONFIRM_SUBMISSION,   # <--- 新增：等待最终确认
    BROWSING_POSTS, 
    BROWSING_COLLECTIONS, 
    COMMENTING,
    DELETING_COMMENT,
    DELETING_WORK
) = range(9)
