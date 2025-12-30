# handlers/approval.py

import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from config import CHANNEL_ID, BOT_USERNAME
from database import get_pool

logger = logging.getLogger(__name__)


async def handle_approval(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """处理审核群的'通过'按钮 (V10.4)"""
    query = update.callback_query
    await query.answer()

    action, user_id_str, message_id_str = query.data.split(':')
    user_id = int(user_id_str)
    message_id = int(message_id_str)
    
    try:
        # 1. 复制消息到频道
        sent_message = await context.bot.copy_message(
            chat_id=CHANNEL_ID,
            from_chat_id=user_id,
            message_id=message_id
        )
        msg_id = sent_message.message_id
        
        # 2. 提取原始内容
        admin_message = query.message
        content_to_save = ""
        original_caption = ""
        
        if admin_message.text:
            content_to_save = admin_message.text
            original_caption = admin_message.text
        elif admin_message.caption:
            caption_parts = admin_message.caption.split('\n\n', 1)
            if len(caption_parts) > 1:
                content_to_save = caption_parts[1]
                original_caption = caption_parts[1]
        
        # 3. 获取投稿者信息并构建页脚
        try:
            submitter = await context.bot.get_chat(user_id)
            author_username = submitter.username or ""
            author_name = submitter.full_name or "匿名用户"
        except:
            author_username = ""
            author_name = "匿名用户"
        
        # 构建页脚链接
        if author_username:
            author_link = f'👤 作者: <a href="https://t.me/{author_username}">{author_name}</a>'
        else:
            author_link = f'👤 作者: <a href="tg://user?id={user_id}">{author_name}</a>'
        
        my_link = f'<a href="https://t.me/{BOT_USERNAME}?start=main">📱 我的</a>'
        footer = f"\n\n━━━━━━━━━━━━━━\n{author_link}  |  {my_link}"
        
        full_caption = (original_caption or "") + footer
        
        # 4. 保存到数据库（只保存原始内容）
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO submissions (user_id, user_name, channel_message_id, content_text) VALUES ($1, $2, $3, $4)",
                user_id, author_name, msg_id, content_to_save
            )
        
        # 5. 编辑频道消息，添加互动按钮（两行布局）
        keyboard = [
            [
                InlineKeyboardButton(f"👍 赞 0", callback_data=f"react:like:{msg_id}"),
                InlineKeyboardButton(f"👎 踩 0", callback_data=f"react:dislike:{msg_id}"),
                InlineKeyboardButton(f"⭐ 收藏 0", callback_data=f"collect:{msg_id}"),
            ],
            [
                InlineKeyboardButton("💬 评论 0", callback_data=f"comment:show:{msg_id}"),
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await context.bot.edit_message_caption(
            chat_id=CHANNEL_ID,
            message_id=msg_id,
            caption=full_caption,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup
        )

        # 6. 更新审核群消息
        original_admin_caption = admin_message.caption or ""
        await query.edit_message_caption(
            caption=f"✅ 已通过 by {query.from_user.first_name}\n\n{original_admin_caption}",
            parse_mode=ParseMode.HTML
        )
        
        # 7. 通知投稿者
        await context.bot.send_message(chat_id=user_id, text="🎉 恭喜！您的作品已被采纳发布。")
        
    except Exception as e:
        logger.error(f"审核通过失败: {e}")
        await query.edit_message_caption(caption=f"❌ 发布失败: {e}", parse_mode=ParseMode.HTML)


async def handle_rejection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """处理审核群的'拒绝'按钮"""
    query = update.callback_query
    await query.answer()

    action, user_id_str, message_id_str = query.data.split(':')
    user_id = int(user_id_str)

    original_caption = query.message.caption or ""
    await query.edit_message_caption(
        caption=f"❌ 已拒绝 by {query.from_user.first_name}\n\n{original_caption}",
        parse_mode=ParseMode.HTML
    )
    
    await context.bot.send_message(chat_id=user_id, text="很抱歉，您的作品未通过审核。")
