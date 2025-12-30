# handlers/start_menu.py

import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from config import CHOOSING

logger = logging.getLogger(__name__)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """总入口函数"""
    # ... (深度链接处理逻辑保持不变，为了节省篇幅省略，请保留原有的深度链接代码) ...
    
    # 如果有深度链接逻辑，请保留在上方

    # 标准流程：显示主菜单
    keyboard = [
        [
            InlineKeyboardButton("✍️ 发布作品", callback_data='submit_post'),
            InlineKeyboardButton("📂 我的作品", callback_data='my_posts_page:1') # 文案修改
        ],
        [
            InlineKeyboardButton("⭐ 我的收藏", callback_data='my_collections_page:1')
        ],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    text = "👋 你好！欢迎来到投稿机器人。\n\n请选择一个操作："
    
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup)
    else:
        await update.message.reply_text(text, reply_markup=reply_markup)
        
    return CHOOSING


async def back_to_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理'返回主菜单'的按钮点击"""
    if update.callback_query:
        await update.callback_query.answer()
    return await start(update, context)
