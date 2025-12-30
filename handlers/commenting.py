# handlers/commenting.py

import logging
from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler
from telegram.constants import ParseMode

from config import COMMENTING, CHANNEL_USERNAME
from database import get_pool

logger = logging.getLogger(__name__)

async def prompt_comment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """提示用户输入评论"""
    message_id = None
    user_id = update.effective_user.id
    
    # 提取参数
    if 'deep_link_message_id' in context.user_data:
        message_id = context.user_data.pop('deep_link_message_id')
    
    if not message_id:
        await context.bot.send_message(chat_id=user_id, text="❌ 错误的评论请求。")
        return ConversationHandler.END

    context.user_data['commenting_on_message_id'] = message_id
    
    # 检查是否是回复特定评论 (Thread)
    # 参数格式: comment_{msg_id}_{parent_comment_id}
    parent_id = context.user_data.pop('reply_to_comment_id', None)
    context.user_data['parent_comment_id'] = parent_id # 存入状态
    
    hint_text = "✍️ 请输入评论内容："
    if parent_id:
        hint_text = "✍️ 请输入您的回复内容："

    await context.bot.send_message(chat_id=user_id, text=f"{hint_text}\n\n(输入 /cancel 可随时取消)")
    return COMMENTING


async def handle_new_comment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """保存评论"""
    user = update.message.from_user
    comment_text = update.message.text
    
    message_id = context.user_data.get('commenting_on_message_id')
    parent_id = context.user_data.get('parent_comment_id') # 获取父评论ID

    if not message_id:
        await update.message.reply_text("❌ 操作超时，请重试。")
        return ConversationHandler.END

    pool = await get_pool()
    async with pool.acquire() as conn:
        # 保存评论 (带 parent_id)
        await conn.execute(
            "INSERT INTO comments (channel_message_id, user_id, user_name, comment_text, parent_id) VALUES ($1, $2, $3, $4, $5)",
            message_id, user.id, user.full_name, comment_text, parent_id
        )
        
        # 获取作者信息用于通知
        post_info = await conn.fetchrow(
            "SELECT user_id, content_text FROM submissions WHERE channel_message_id = $1",
            message_id
        )

    await update.message.reply_text("✅ 评论/回复成功！")

    # 通知逻辑 (通知楼主)
    if post_info:
        author_id = post_info['user_id']
        content_text = post_info['content_text']
        if author_id != user.id:
            post_url = f"https://t.me/{CHANNEL_USERNAME}/{message_id}"
            actor = f'<a href="tg://user?id={user.id}">{user.full_name}</a>'
            preview = (content_text or "作品")[:20]
            msg = f"💬 {actor} 评论了你的作品 <a href='{post_url}'>{preview}</a>\n内容：{comment_text}"
            try: await context.bot.send_message(chat_id=author_id, text=msg, parse_mode=ParseMode.HTML)
            except: pass
            
    # 如果是回复别人的评论，也可以通知那个人 (可选优化，此处暂略)

    context.user_data.clear()
    return ConversationHandler.END
