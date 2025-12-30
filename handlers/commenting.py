# handlers/commenting.py

import logging
from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler
from telegram.constants import ParseMode
from telegram.error import TelegramError

from config import COMMENTING, CHANNEL_USERNAME
from database import get_pool

logger = logging.getLogger(__name__)

async def prompt_comment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """提示用户输入评论"""
    message_id = None
    user_id = update.effective_user.id
    
    if 'deep_link_message_id' in context.user_data:
        message_id = context.user_data.pop('deep_link_message_id')
    
    if not message_id:
        await context.bot.send_message(chat_id=user_id, text="❌ 错误的评论请求。")
        return ConversationHandler.END

    context.user_data['commenting_on_message_id'] = message_id
    
    await context.bot.send_message(
        chat_id=user_id,
        text="✍️ 您正在发表评论，请输入内容：\n\n(输入 /cancel 可随时取消)"
    )
    return COMMENTING


async def handle_new_comment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """接收用户的评论文本，存入数据库，并通知作者"""
    user = update.message.from_user
    comment_text = update.message.text
    
    message_id = context.user_data.get('commenting_on_message_id')

    if not message_id:
        await update.message.reply_text("❌ 操作超时或出现错误，请回到频道重试。")
        return ConversationHandler.END

    pool = await get_pool()
    async with pool.acquire() as conn:
        # 保存评论
        await conn.execute(
            "INSERT INTO comments (channel_message_id, user_id, user_name, comment_text) VALUES ($1, $2, $3, $4)",
            message_id, user.id, user.full_name, comment_text
        )
        
        # 获取作者信息并发送通知
        post_info = await conn.fetchrow(
            "SELECT user_id, content_text FROM submissions WHERE channel_message_id = $1",
            message_id
        )

    await update.message.reply_text("✅ 评论成功！\n\n帖子的评论数将在下次有人互动时更新。")

    # 发送通知给作者
    if post_info:
        author_id = post_info['user_id']
        content_text = post_info['content_text']
        
        # 不给自己发通知
        if author_id != user.id:
            post_url = f"https://t.me/{CHANNEL_USERNAME}/{message_id}"
            actor_link = f'<a href="tg://user?id={user.id}">{user.full_name}</a>'
            
            preview_text = (content_text or "你的作品")[:30]
            preview_text = preview_text.replace('<', '&lt;').replace('>', '&gt;')
            if len(content_text or "") > 30:
                preview_text += "..."
            post_link = f'<a href="{post_url}">{preview_text}</a>'
            
            notification_message = f"💬 {actor_link} 评论了你的作品 {post_link}\n\n评论内容：{comment_text[:50]}{'...' if len(comment_text) > 50 else ''}"
            
            try:
                await context.bot.send_message(
                    chat_id=author_id,
                    text=notification_message,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=False
                )
                logger.info(f"评论通知已发送给作者 {author_id}")
            except TelegramError as e:
                logger.warning(f"发送评论通知失败: {e}")

    context.user_data.clear()
    return ConversationHandler.END
