# handlers/comment_management.py

import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from telegram.constants import ParseMode

from config import CHANNEL_USERNAME, DELETING_COMMENT
from database import get_pool

logger = logging.getLogger(__name__)


async def show_delete_comment_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """显示删除评论菜单"""
    user_id = update.effective_user.id
    
    if update.message:
        message = update.message
    elif update.callback_query:
        message = update.callback_query.message
        user_id = update.callback_query.from_user.id
    else:
        return ConversationHandler.END
    
    if not context.args or not context.args[0].startswith('manage_comments_'):
        await message.reply_text("❌ 无效的请求。")
        return ConversationHandler.END
    
    try:
        message_id = int(context.args[0].replace('manage_comments_', ''))
    except ValueError:
        await message.reply_text("❌ 无效的帖子ID。")
        return ConversationHandler.END
    
    pool = await get_pool()
    async with pool.acquire() as conn:
        post_info = await conn.fetchrow(
            "SELECT user_id FROM submissions WHERE channel_message_id = $1",
            message_id
        )
        
        if not post_info:
            await message.reply_text("❌ 帖子不存在。")
            return ConversationHandler.END
        
        author_id = post_info['user_id']
        is_author = (user_id == author_id)
        
        # 查询用户自己的评论
        my_comments_rows = await conn.fetch(
            "SELECT id, comment_text, timestamp FROM comments WHERE channel_message_id = $1 AND user_id = $2 ORDER BY timestamp DESC",
            message_id, user_id
        )
        
        other_comments_rows = []
        if is_author:
            other_comments_rows = await conn.fetch(
                "SELECT id, user_id, user_name, comment_text, timestamp FROM comments WHERE channel_message_id = $1 AND user_id != $2 ORDER BY timestamp DESC",
                message_id, user_id
            )
    
    # 转换 Records 为字典映射，方便通过序号查找 ID
    # my_comments_rows 是 Record 列表，可以直接解包 (id, text, timestamp)
    
    # 保存映射到 context
    context.user_data['delete_mode'] = {
        'message_id': message_id,
        'my_comments': {str(idx): row['id'] for idx, row in enumerate(my_comments_rows, 1)},
        'other_comments': {str(idx): row['id'] for idx, row in enumerate(other_comments_rows, 1)} if is_author else {},
        'is_author': is_author
    }
    
    # 构建消息文本
    message_text = "🗑️ <b>删除评论</b>\n\n"
    
    if my_comments_rows:
        message_text += "📝 <b>你的评论：</b>\n"
        for idx, row in enumerate(my_comments_rows, 1):
            text = row['comment_text']
            preview = text[:80] + "..." if len(text) > 80 else text
            preview = preview.replace('<', '&lt;').replace('>', '&gt;')
            message_text += f"\n<b>{idx}.</b> {preview}\n"
    else:
        message_text += "📝 <b>你的评论：</b> 暂无评论\n"
    
    if is_author:
        message_text += "\n━━━━━━━━━━━━━━\n\n"
        if other_comments_rows:
            message_text += "👥 <b>其他人的评论：</b>\n"
            start_num = len(my_comments_rows) + 1
            for idx, row in enumerate(other_comments_rows, start_num):
                uname = row['user_name']
                text = row['comment_text']
                preview = text[:80] + "..." if len(text) > 80 else text
                preview = preview.replace('<', '&lt;').replace('>', '&gt;')
                message_text += f"\n<b>{idx}.</b> <b>{uname}:</b> {preview}\n"
        else:
            message_text += "👥 <b>其他人的评论：</b> 暂无\n"
    
    message_text += "\n━━━━━━━━━━━━━━\n\n"
    message_text += "💡 <b>如何删除？</b>\n"
    if my_comments_rows:
        message_text += "• 发送数字删除你的评论（如：<code>1</code>）\n"
    if is_author and other_comments_rows:
        message_text += f"• 发送数字删除其他评论（如：<code>{len(my_comments_rows) + 1}</code>）\n"
    message_text += "• 发送 /cancel 取消操作"
    
    post_url = f"https://t.me/{CHANNEL_USERNAME}/{message_id}"
    keyboard = [[InlineKeyboardButton("↩️ 返回帖子", url=post_url)]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        message_text,
        parse_mode=ParseMode.HTML,
        reply_markup=reply_markup
    )
    
    return DELETING_COMMENT


async def handle_delete_comment_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """处理用户输入的评论编号"""
    
    await update.message.reply_text(f"🔍 DEBUG: 收到消息 '{update.message.text}'")
    
    user_id = update.message.from_user.id
    text = update.message.text.strip()
    
    delete_data = context.user_data.get('delete_mode')
    if not delete_data:
        await update.message.reply_text("❌ 会话已过期，请重新进入删除模式。")
        return ConversationHandler.END
    
    message_id = delete_data['message_id']
    my_comments = delete_data['my_comments']
    other_comments = delete_data['other_comments']
    is_author = delete_data['is_author']
    
    if not text.isdigit():
        await update.message.reply_text("❌ 请发送评论编号（数字）。")
        return DELETING_COMMENT
    
    comment_id = None
    comment_owner = None
    
    input_num = int(text)
    my_comment_count = len(my_comments)
    
    if input_num <= my_comment_count and str(input_num) in my_comments:
        comment_id = my_comments[str(input_num)]
        comment_owner = "你的"
    elif is_author and input_num > my_comment_count:
        other_index = input_num - my_comment_count
        if str(other_index) in other_comments:
            comment_id = other_comments[str(other_index)]
            comment_owner = "其他人的"
    
    if not comment_id:
        total_count = len(my_comments) + (len(other_comments) if is_author else 0)
        await update.message.reply_text(f"❌ 评论编号 {text} 不存在。请发送 1-{total_count} 之间的数字。")
        return DELETING_COMMENT
    
    pool = await get_pool()
    async with pool.acquire() as conn:
        comment_info = await conn.fetchrow(
            """
            SELECT c.user_id, c.comment_text, c.user_name, s.user_id as author_id 
            FROM comments c JOIN submissions s ON c.channel_message_id = s.channel_message_id 
            WHERE c.id = $1
            """,
            comment_id
        )
        
        if not comment_info:
            await update.message.reply_text("❌ 评论不存在或已被删除。")
            return ConversationHandler.END
        
        comment_user_id = comment_info['user_id']
        comment_text = comment_info['comment_text']
        post_author_id = comment_info['author_id']
        
        if user_id != comment_user_id and user_id != post_author_id:
            await update.message.reply_text("❌ 你没有权限删除这条评论。")
            return ConversationHandler.END
        
        await conn.execute("DELETE FROM comments WHERE id = $1", comment_id)
    
    preview = comment_text[:50] + "..." if len(comment_text) > 50 else comment_text
    await update.message.reply_text(
        f"✅ 已删除{comment_owner}评论\n\n"
        f"内容：{preview}\n\n"
        f"继续发送编号可删除更多评论，或发送 /cancel 结束。"
    )
    
    context.args = [f"manage_comments_{message_id}"]
    await show_delete_comment_menu(update, context)
    
    return DELETING_COMMENT
