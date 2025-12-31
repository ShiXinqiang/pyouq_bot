# handlers/start_menu.py

import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from config import CHOOSING, CHANNEL_ID, CHANNEL_USERNAME
from .channel_interact import build_threaded_comment_section
from database import get_pool

logger = logging.getLogger(__name__)

async def update_thread_view(context, message_id, expanded_cid=None):
    """更新频道消息（展开/收起楼中楼）"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        db_row = await conn.fetchrow("SELECT content_text, user_id, user_name FROM submissions WHERE channel_message_id = $1", message_id)
        if not db_row: return
        
        # 重建页脚
        content = db_row['content_text']
        author_id = db_row['user_id']
        try: u_name = (await context.bot.get_chat(author_id)).username or ""
        except: u_name = ""
        
        author_link = f'👤 作者: <a href="https://t.me/{u_name}">{db_row["user_name"]}</a>' if u_name else f'👤 作者: <a href="tg://user?id={author_id}">{db_row["user_name"]}</a>'
        my_link = f'<a href="https://t.me/{context.bot.username}?start=main">📱 我的</a>'
        base_caption = (content or "") + f"\n\n━━━━━━━━━━━━━━\n{author_link}  |  {my_link}"
        
        # 构建评论内容
        c_text = await build_threaded_comment_section(conn, message_id, expanded_comment_id=expanded_cid)
        final_caption = base_caption + c_text
        
        # 保持火标
        is_pinned = await conn.fetchval("SELECT id FROM pinned_posts WHERE channel_message_id = $1", message_id)
        if is_pinned and not final_caption.startswith("🔥"):
            final_caption = "🔥 " + final_caption

        # === 按钮栏 (展开状态下：不显示点赞栏) ===
        add_url = f"https://t.me/{context.bot.username}?start=comment_{message_id}"
        del_url = f"https://t.me/{context.bot.username}?start=manage_comments_{message_id}"
        
        row_ops = [
            InlineKeyboardButton("✍️ 发表", url=add_url),
            InlineKeyboardButton("🗑️ 删除", url=del_url),
            InlineKeyboardButton("🔄 刷新", callback_data=f"comment:refresh:{message_id}")
        ]
        row_close = [InlineKeyboardButton("⬆️ 收起", callback_data=f"comment:hide:{message_id}")]
        
        markup = InlineKeyboardMarkup([row_ops, row_close])
        
        try:
            await context.bot.edit_message_caption(
                chat_id=CHANNEL_ID,
                message_id=message_id,
                caption=final_caption,
                parse_mode=ParseMode.HTML,
                reply_markup=markup
            )
        except Exception as e:
            logger.error(f"Thread update error: {e}")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """总入口"""
    if context.args:
        payload = context.args[0]
        
        # 1. 展开/收起楼中楼 (静默操作)
        if payload.startswith("thread_expand_") or payload.startswith("thread_collapse_"):
            try:
                parts = payload.split("_")
                msg_id = int(parts[2])
                
                # 区分是展开还是收起
                if "expand" in payload:
                    cid = int(parts[3])
                    await update_thread_view(context, msg_id, expanded_cid=cid)
                    text = "✅ 已展开回复。"
                else:
                    await update_thread_view(context, msg_id, expanded_cid=None)
                    text = "✅ 已收起回复。"
                
                # 【修复】返回按钮使用标准链接
                # 注意：CHANNEL_USERNAME 必须在 .env 里配置正确，不带 @
                post_url = f"https://t.me/{CHANNEL_USERNAME}/{msg_id}"
                
                await update.message.reply_text(
                    text, 
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ 返回频道查看", url=post_url)]])
                )
            except Exception as e:
                logger.error(f"Thread action failed: {e}")
            return CHOOSING

        # 2. 评论/回复
        elif payload.startswith("comment_"):
            from .commenting import prompt_comment
            parts = payload.split("_")
            try:
                context.user_data['deep_link_message_id'] = int(parts[1])
                if len(parts) > 2:
                    context.user_data['reply_to_comment_id'] = int(parts[2])
                return await prompt_comment(update, context)
            except: pass
            
        # 3. 删除评论
        elif payload.startswith("manage_comments_"):
            from .comment_management import show_delete_comment_menu
            return await show_delete_comment_menu(update, context)

    # 主菜单
    kb = [[InlineKeyboardButton("✍️ 发布作品", callback_data='submit_post'), InlineKeyboardButton("📂 我的作品", callback_data='my_posts_page:1')], [InlineKeyboardButton("⭐ 我的收藏", callback_data='my_collections_page:1')]]
    text = "👋 你好！欢迎使用发布助手。\n\n请选择一个操作："
    if update.callback_query: await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))
    else: await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb))
    return CHOOSING

async def back_to_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query: await update.callback_query.answer()
    return await start(update, context)
