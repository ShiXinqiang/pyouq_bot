# handlers/start_menu.py

import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from config import CHOOSING, CHANNEL_ID
# 引入新写的构建函数
from .channel_interact import build_threaded_comment_section, get_all_counts
from database import get_pool

logger = logging.getLogger(__name__)

async def update_thread_view(context, message_id, expanded_cid=None):
    """更新频道消息，展开指定评论"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        db_row = await conn.fetchrow("SELECT content_text, user_id, user_name FROM submissions WHERE channel_message_id = $1", message_id)
        if not db_row: return
        
        # 重建头部
        author_link = f'👤 作者: <a href="tg://user?id={db_row["user_id"]}">{db_row["user_name"]}</a>'
        my_link = f'<a href="https://t.me/{context.bot.username}?start=main">📱 我的</a>'
        base_caption = (db_row['content_text'] or "") + f"\n\n━━━━━━━━━━━━━━\n{author_link}  |  {my_link}"
        
        # 构建评论区 (传入展开ID)
        c_text = await build_threaded_comment_section(conn, message_id, expanded_comment_id=expanded_cid)
        final_caption = base_caption + c_text
        
        # 构建按钮 (保持打开状态)
        counts = await get_all_counts(conn, message_id)
        row1 = [
            InlineKeyboardButton(f"👍 赞 {counts['likes']}", callback_data=f"react:like:{message_id}"),
            InlineKeyboardButton(f"👎 踩 {counts['dislikes']}", callback_data=f"react:dislike:{message_id}"),
            InlineKeyboardButton(f"⭐ 收藏 {counts['collections']}", callback_data=f"collect:{message_id}"),
        ]
        add_url = f"https://t.me/{context.bot.username}?start=comment_{message_id}"
        del_url = f"https://t.me/{context.bot.username}?start=manage_comments_{message_id}"
        row2 = [
            InlineKeyboardButton("✍️ 发表", url=add_url),
            InlineKeyboardButton("🗑️ 删除", url=del_url),
            InlineKeyboardButton("🔄 刷新", callback_data=f"comment:refresh:{message_id}")
        ]
        row3 = [InlineKeyboardButton("⬆️ 收起", callback_data=f"comment:hide:{message_id}")]
        
        try:
            await context.bot.edit_message_caption(
                chat_id=CHANNEL_ID,
                message_id=message_id,
                caption=final_caption,
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([row1, row2, row3])
            )
        except Exception as e:
            logger.error(f"Thread update error: {e}")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """总入口"""
    if context.args:
        payload = context.args[0]
        
        # 1. 展开某个评论的子楼层
        # 格式: thread_expand_{msg_id}_{comment_id}
        if payload.startswith("thread_expand_"):
            try:
                _, _, msg_id_str, cid_str = payload.split("_")
                await update_thread_view(context, int(msg_id_str), expanded_cid=int(cid_str))
                # 这是一个“静默操作”，不需要给用户发很多文字，稍微提示即可
                await update.message.reply_text("✅ 已展开回复。", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ 返回频道", url=f"https://t.me/{CHANNEL_ID}")]])) # 注意这里需要真实的频道链接，或者只提示
                return CHOOSING
            except: pass
            
        # 2. 收起子楼层 (恢复默认)
        # 格式: thread_collapse_{msg_id}
        elif payload.startswith("thread_collapse_"):
            try:
                msg_id = int(payload.split("_")[2])
                await update_thread_view(context, msg_id, expanded_cid=None)
                await update.message.reply_text("✅ 已收起回复。")
                return CHOOSING
            except: pass

        # 3. 评论/回复
        # 格式: comment_{msg_id} 或 comment_{msg_id}_{parent_id}
        elif payload.startswith("comment_"):
            from .commenting import prompt_comment
            parts = payload.split("_")
            try:
                context.user_data['deep_link_message_id'] = int(parts[1])
                if len(parts) > 2:
                    context.user_data['reply_to_comment_id'] = int(parts[2]) # 存入父评论ID
                return await prompt_comment(update, context)
            except: pass
            
        # 4. 删除评论
        elif payload.startswith("manage_comments_"):
            from .comment_management import show_delete_comment_menu
            return await show_delete_comment_menu(update, context)

    # 默认菜单
    # ... (保持之前的菜单代码) ...
    # 为了完整性，这里简写
    kb = [[InlineKeyboardButton("✍️ 发布作品", callback_data='submit_post'), InlineKeyboardButton("📂 我的作品", callback_data='my_posts_page:1')], [InlineKeyboardButton("⭐ 我的收藏", callback_data='my_collections_page:1')]]
    text = "👋 你好！欢迎使用发布助手。"
    if update.callback_query: await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))
    else: await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb))
    return CHOOSING

async def back_to_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query: await update.callback_query.answer()
    return await start(update, context)
