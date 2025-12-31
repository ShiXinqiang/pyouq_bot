# handlers/commenting.py

import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
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
    
    # 1. 解析参数
    if 'deep_link_message_id' in context.user_data:
        message_id = context.user_data.pop('deep_link_message_id')
    
    if not message_id:
        await context.bot.send_message(chat_id=user_id, text="❌ 错误的评论请求。")
        return ConversationHandler.END

    # 2. 存入状态
    context.user_data['commenting_on_message_id'] = message_id
    
    # 检查是否是回复特定评论 (Thread)
    parent_id = context.user_data.pop('reply_to_comment_id', None)
    context.user_data['parent_comment_id'] = parent_id 
    
    # 3. 构建带有“返回”按钮的提示消息
    # 这样如果用户点错了进来，不用输入 /cancel 也能直接点按钮回去
    post_url = f"https://t.me/{CHANNEL_USERNAME}/{message_id}"
    keyboard = [[InlineKeyboardButton("⬅️ 取消并返回帖子", url=post_url)]]
    
    hint_text = "✍️ <b>请输入评论内容：</b>"
    if parent_id:
        hint_text = "✍️ <b>请输入您的回复内容：</b>"

    await context.bot.send_message(
        chat_id=user_id, 
        text=f"{hint_text}\n\n(或者点击下方按钮返回)",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return COMMENTING


async def handle_new_comment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """保存评论并提供返回按钮"""
    user = update.message.from_user
    comment_text = update.message.text
    
    message_id = context.user_data.get('commenting_on_message_id')
    parent_id = context.user_data.get('parent_comment_id')

    if not message_id:
        await update.message.reply_text("❌ 会话已过期，请重新从频道点击评论。")
        return ConversationHandler.END

    pool = await get_pool()
    async with pool.acquire() as conn:
        # 保存评论
        await conn.execute(
            "INSERT INTO comments (channel_message_id, user_id, user_name, comment_text, parent_id) VALUES ($1, $2, $3, $4, $5)",
            message_id, user.id, user.full_name, comment_text, parent_id
        )
        
        # 获取作者信息用于通知
        post_info = await conn.fetchrow(
            "SELECT user_id, content_text FROM submissions WHERE channel_message_id = $1",
            message_id
        )

    # === 核心修改：发送带有返回按钮的成功消息 ===
    post_url = f"https://t.me/{CHANNEL_USERNAME}/{message_id}"
    
    # 如果是楼中楼回复，文字稍微区分一下
    success_text = "✅ <b>回复成功！</b>" if parent_id else "✅ <b>评论成功！</b>"
    
    keyboard = [
        [InlineKeyboardButton("⬅️ 返回刚才的帖子", url=post_url)],
        # 如果你想做得更细致，还可以加一个返回主菜单
        # [InlineKeyboardButton("🏠 返回机器人主页", callback_data='back_to_main')] 
    ]
    
    await update.message.reply_text(
        success_text, 
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

    # --- 通知逻辑 (通知楼主) ---
    if post_info:
        author_id = post_info['user_id']
        content_text = post_info['content_text']
        # 不通知自己
        if author_id != user.id:
            actor = f'<a href="tg://user?id={user.id}">{user.full_name}</a>'
            preview = (content_text or "作品")[:20].replace('<', '&lt;').replace('>', '&gt;')
            
            # 这里的链接也做成跳回频道的
            msg = f"💬 {actor} 评论了你的作品 <a href='{post_url}'>{preview}</a>\n\n内容：{comment_text}"
            try: 
                await context.bot.send_message(chat_id=author_id, text=msg, parse_mode=ParseMode.HTML)
            except: 
                pass

    context.user_data.clear()
    return ConversationHandler.END
