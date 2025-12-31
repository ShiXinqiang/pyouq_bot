# handlers/channel_interact.py

import logging
from typing import Tuple, Dict
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import ContextTypes
from telegram.error import TelegramError

from config import BOT_USERNAME, CHANNEL_USERNAME, CHANNEL_ID
from database import get_pool

logger = logging.getLogger(__name__)


async def check_and_pin_if_hot(context: ContextTypes.DEFAULT_TYPE, message_id: int, like_count: int):
    """检查点赞数，如果达到100自动置顶"""
    if like_count < 100: return
    
    pool = await get_pool()
    async with pool.acquire() as conn:
        already_pinned = await conn.fetchval("SELECT id FROM pinned_posts WHERE channel_message_id = $1", message_id)
        if already_pinned: return
        
        try:
            await context.bot.pin_chat_message(chat_id=CHANNEL_ID, message_id=message_id, disable_notification=True)
            await conn.execute("INSERT INTO pinned_posts (channel_message_id, like_count_at_pin) VALUES ($1, $2)", message_id, like_count)
            
            # 通知作者
            post_info = await conn.fetchrow("SELECT user_id, content_text FROM submissions WHERE channel_message_id = $1", message_id)
            if post_info:
                author_id = post_info['user_id']
                content_text = post_info['content_text']
                post_url = f"https://t.me/{CHANNEL_USERNAME}/{message_id}"
                preview_text = (content_text or "作品")[:20].replace('<', '&lt;').replace('>', '&gt;') + "..."
                msg = f"🔥 <b>恭喜！作品火了！</b>\n<a href='{post_url}'>{preview_text}</a> 获赞 {like_count}，已自动置顶！"
                try: await context.bot.send_message(chat_id=author_id, text=msg, parse_mode=ParseMode.HTML)
                except: pass
        except: pass


async def get_all_counts(conn, message_id: int) -> Dict[str, int]:
    rows = await conn.fetch("SELECT reaction_type, COUNT(*) as count FROM reactions WHERE channel_message_id = $1 GROUP BY reaction_type", message_id)
    counts = {row['reaction_type']: row['count'] for row in rows}
    return {
        "likes": counts.get(1, 0),
        "dislikes": counts.get(-1, 0),
        "comments": await conn.fetchval("SELECT COUNT(*) FROM comments WHERE channel_message_id = $1", message_id) or 0,
        "collections": await conn.fetchval("SELECT COUNT(*) FROM collections WHERE channel_message_id = $1", message_id) or 0,
    }


async def build_threaded_comment_section(conn, message_id: int, expanded_comment_id: int = None) -> str:
    """构建楼中楼评论区"""
    # 获取主评论
    top_comments = await conn.fetch(
        "SELECT id, user_id, user_name, comment_text FROM comments WHERE channel_message_id = $1 AND parent_id IS NULL ORDER BY timestamp ASC",
        message_id
    )
    total_count = await conn.fetchval("SELECT COUNT(*) FROM comments WHERE channel_message_id = $1", message_id) or 0
    
    if not top_comments:
        return "\n\n--- 评论区 ---\n✨ 暂无评论，快来抢沙发吧！"
    
    text = f"\n\n--- 评论区 ({total_count}条) ---\n"
    
    for idx, top in enumerate(top_comments, 1):
        cid = top['id']
        uid = top['user_id']
        uname = top['user_name'].replace('<', '&lt;')
        content = top['comment_text'].replace('<', '&lt;')
        
        # 查询回复
        replies = await conn.fetch("SELECT id, user_name, comment_text FROM comments WHERE parent_id = $1 ORDER BY timestamp ASC", cid)
        reply_count = len(replies)
        
        is_expanded = (cid == expanded_comment_id)
        action_link = ""
        
        if reply_count > 2 and not is_expanded:
            link = f"https://t.me/{BOT_USERNAME}?start=thread_expand_{message_id}_{cid}"
            action_link = f"<a href='{link}'>:展开</a>"
        else:
            link = f"https://t.me/{BOT_USERNAME}?start=comment_{message_id}_{cid}"
            action_link = f"<a href='{link}'>:回复</a>"
            
        text += f"<b>{idx}. {uname}:</b> {content} {action_link}\n"
        
        # 子回复显示逻辑
        replies_to_show = []
        show_collapse_btn = False
        
        if reply_count > 0:
            if reply_count <= 2:
                replies_to_show = replies
            elif is_expanded:
                replies_to_show = replies
                show_collapse_btn = True
            else:
                # 未展开且超过2条，不显示子回复(折叠)，只显示主楼的:展开
                replies_to_show = [] 
        
        for r in replies_to_show:
            r_name = r['user_name'].replace('<', '&lt;')
            r_text = r['comment_text'].replace('<', '&lt;')
            text += f"   └ {r_name}: {r_text}\n"
            
        if show_collapse_btn:
            link = f"https://t.me/{BOT_USERNAME}?start=thread_collapse_{message_id}"
            text += f"   <a href='{link}'>⬆️ 收起</a>\n"
            
    return text


async def send_notification(context: ContextTypes.DEFAULT_TYPE, author_id: int, actor_id: int, actor_name: str, 
                            message_id: int, content_preview: str, action_type: str):
    if author_id == actor_id: return
    post_url = f"https://t.me/{CHANNEL_USERNAME}/{message_id}"
    actor_link = f'<a href="tg://user?id={actor_id}">{actor_name}</a>'
    preview = (content_preview or "作品")[:20].replace('<', '&lt;').replace('>', '&gt;') + "..."
    post_link = f'<a href="{post_url}">{preview}</a>'
    
    msgs = {
        "like": f"👍 {actor_link} 赞了你的作品 {post_link}",
        "collect": f"⭐ {actor_link} 收藏了你的作品 {post_link}",
        "comment": f"💬 {actor_link} 评论了你的作品 {post_link}"
    }
    if action_type in msgs:
        try: await context.bot.send_message(chat_id=author_id, text=msgs[action_type], parse_mode=ParseMode.HTML)
        except: pass


async def handle_channel_interaction(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    message_id = query.message.message_id
    data = query.data.split(':')
    action = data[0]
    
    pool = await get_pool()
    async with pool.acquire() as conn:
        db_row = await conn.fetchrow("SELECT content_text, user_id, user_name FROM submissions WHERE channel_message_id = $1", message_id)
        if db_row:
            content = db_row['content_text']
            author_id = db_row['user_id']
            try: u_name = (await context.bot.get_chat(author_id)).username or ""
            except: u_name = ""
            author_link = f'👤 作者: <a href="https://t.me/{u_name}">{db_row["user_name"]}</a>' if u_name else f'👤 作者: <a href="tg://user?id={author_id}">{db_row["user_name"]}</a>'
            my_link = f'<a href="https://t.me/{BOT_USERNAME}?start=main">📱 我的</a>'
            base_caption = (content or "") + f"\n\n━━━━━━━━━━━━━━\n{author_link}  |  {my_link}"
        else:
            base_caption = (query.message.caption_html or "").split("\n\n--- 评论区 ---")[0]
            author_id = None
            content = ""

        # 处理动作
        notify_type = None
        check_pin = False
        show_comments = False
        
        # 判断当前状态
        if "--- 评论区" in (query.message.caption or ""): show_comments = True
            
        if action == 'comment':
            sub = data[1]
            if sub == 'show' or sub == 'refresh': show_comments = True
            elif sub == 'hide': show_comments = False
        
        elif action == 'react':
            rtype = data[1]
            val = 1 if rtype == 'like' else -1
            curr = await conn.fetchval("SELECT reaction_type FROM reactions WHERE channel_message_id = $1 AND user_id = $2", message_id, user_id)
            if curr is None:
                await conn.execute("INSERT INTO reactions (channel_message_id, user_id, reaction_type) VALUES ($1, $2, $3)", message_id, user_id, val)
                if rtype == 'like': notify_type = "like"; check_pin = True
            elif curr == val:
                await conn.execute("DELETE FROM reactions WHERE channel_message_id = $1 AND user_id = $2", message_id, user_id)
            else:
                await conn.execute("UPDATE reactions SET reaction_type = $1 WHERE channel_message_id = $2 AND user_id = $3", val, message_id, user_id)
                if rtype == 'like': notify_type = "like"; check_pin = True
        
        elif action == 'collect':
            cid = await conn.fetchval("SELECT id FROM collections WHERE channel_message_id = $1 AND user_id = $2", message_id, user_id)
            if cid: await conn.execute("DELETE FROM collections WHERE id = $1", cid)
            else: await conn.execute("INSERT INTO collections (channel_message_id, user_id) VALUES ($1, $2)", message_id, user_id); notify_type = "collect"

        if notify_type and author_id:
            await send_notification(context, author_id, user_id, query.from_user.full_name, message_id, content, notify_type)

        # 构建最终文案
        final_caption = base_caption
        if show_comments:
            # 默认不展开任何楼中楼
            c_text = await build_threaded_comment_section(conn, message_id, expanded_comment_id=None)
            final_caption += c_text

        # 4. 构建按钮 (重点修复)
        counts = await get_all_counts(conn, message_id)
        
        if not show_comments:
            # === 模式 A: 收起状态 ===
            # 显示 [点赞栏] 和 [评论按钮]
            row1 = [
                InlineKeyboardButton(f"👍 赞 {counts['likes']}", callback_data=f"react:like:{message_id}"),
                InlineKeyboardButton(f"👎 踩 {counts['dislikes']}", callback_data=f"react:dislike:{message_id}"),
                InlineKeyboardButton(f"⭐ 收藏 {counts['collections']}", callback_data=f"collect:{message_id}"),
            ]
            row2 = [InlineKeyboardButton(f"💬 评论 {counts['comments']}", callback_data=f"comment:show:{message_id}")]
            markup = InlineKeyboardMarkup([row1, row2])
            
        else:
            # === 模式 B: 阅读评论状态 ===
            # 【修复】隐藏点赞栏，只显示功能键
            add_url = f"https://t.me/{BOT_USERNAME}?start=comment_{message_id}"
            del_url = f"https://t.me/{BOT_USERNAME}?start=manage_comments_{message_id}"
            
            row1 = [
                InlineKeyboardButton("✍️ 发表", url=add_url),
                InlineKeyboardButton("🗑️ 删除", url=del_url),
                InlineKeyboardButton("🔄 刷新", callback_data=f"comment:refresh:{message_id}")
            ]
            row2 = [InlineKeyboardButton("⬆️ 收起", callback_data=f"comment:hide:{message_id}")]
            markup = InlineKeyboardMarkup([row1, row2])

        if check_pin and counts['likes'] >= 100:
            await check_and_pin_if_hot(context, message_id, counts['likes'])
            if not final_caption.startswith("🔥"): final_caption = "🔥 " + final_caption

        if final_caption != query.message.caption_html or markup != query.message.reply_markup:
            try: await query.edit_message_caption(caption=final_caption, parse_mode=ParseMode.HTML, reply_markup=markup)
            except: pass
