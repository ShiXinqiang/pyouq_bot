# handlers/start_menu.py

import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from config import CHOOSING

logger = logging.getLogger(__name__)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    总入口函数，处理普通 /start 和深度链接 /start
    """
    # --- 1. 深度链接处理 (Deep Linking) ---
    # 用于从频道点击“评论”或“删除评论”按钮跳转回机器人时直达对应功能
    if context.args and len(context.args) > 0:
        payload = context.args[0]
        
        logger.info(f"收到深度链接: {payload}")
        
        # 评论深度链接
        if payload.startswith("comment_"):
            from .commenting import prompt_comment
            message_id_str = payload.split("_", 1)[1]
            try:
                message_id = int(message_id_str)
                context.user_data['deep_link_message_id'] = message_id
                logger.info(f"进入评论模式，帖子ID: {message_id}")
                return await prompt_comment(update, context)
            except (IndexError, ValueError) as e:
                logger.error(f"解析评论链接失败: {e}")
        
        # 管理评论深度链接
        elif payload.startswith("manage_comments_"):
            from .comment_management import show_delete_comment_menu
            logger.info(f"进入删除评论模式: {payload}")
            # 直接返回函数的返回值，让状态正确传递
            return await show_delete_comment_menu(update, context)

    # --- 2. 标准流程：显示主菜单 ---
    keyboard = [
        [
            InlineKeyboardButton("✍️ 发布作品", callback_data='submit_post'),
            InlineKeyboardButton("📂 我的作品", callback_data='my_posts_page:1')
        ],
        [
            InlineKeyboardButton("⭐ 我的收藏", callback_data='my_collections_page:1')
        ],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    text = "👋 你好！欢迎使用发布助手。\n\n请选择一个操作："
    
    if update.callback_query:
        # 如果是点击“返回主菜单”回来的，编辑原消息
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup)
    else:
        # 如果是输入 /start 进来的，发送新消息
        await update.message.reply_text(text, reply_markup=reply_markup)
        
    return CHOOSING


async def back_to_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理'返回主菜单'的按钮点击"""
    if update.callback_query:
        await update.callback_query.answer()
    return await start(update, context)
