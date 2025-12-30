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
    """
    构建楼中楼评论区
    expanded_comment_id: 当前被用户点击展开的那个主评论ID
    """
    # 1. 获取所有主评论 (parent_id IS NULL)
    # 按时间正序(最早在最前)或者倒序，这里用最早在最前，符合楼层习惯
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
        
        # 查询该主评论下的回复
        replies = await conn.fetch(
            "SELECT id, user_name, comment_text FROM comments WHERE parent_id = $1 ORDER BY timestamp ASC",
            cid
        )
        reply_count = len(replies)
        
        # 构造主评论行
        # 逻辑：如果回复超过2条且未展开 -> 显示 :展开
        #      其他情况 -> 显示 :回复
        action_link = ""
        is_expanded = (cid == expanded_comment_id)
        
        if reply_count > 2 and not is_expanded:
            # 显示 [展开] 链接
            # 格式: thread_expand_{msg_id}_{comment_id}
            link = f"https://t.me/{BOT_USERNAME}?start=thread_expand_{message_id}_{cid}"
            action_link = f"<a href='{link}'>:展开</a>"
        else:
            # 显示 [回复] 链接
            # 格式: comment_{msg_id}_{comment_id} (最后这个是 parent_id)
            link = f"https://t.me/{BOT_USERNAME}?start=comment_{message_id}_{cid}"
            action_link = f"<a href='{link}'>:回复</a>"
            
        text += f"<b>{idx}. {uname}:</b> {content} {action_link}\n"
        
        # 处理子回复显示
        replies_to_show = []
        show_collapse_btn = False
        
        if reply_count == 0:
            pass
        elif reply_count <= 2:
            # 少于2条，全部显示
            replies_to_show = replies
        else:
            # 超过2条
            if is_expanded:
                # 已展开：显示全部 + 收起按钮
                replies_to_show = replies
                show_collapse_btn = True
            else:
                # 未展开：不显示子回复 (根据你的需求: "超过2条折叠内容")
                # 或者你想要显示前2条？你的描述是 "超过2条折叠内容... 展开之后... 显示回复内容"
                # 按照你的示例：未展开时，主评论后面是 :展开，下面没有子回复。
                replies_to_show = [] 
        
        # 渲染子回复
        for r in replies_to_show:
            r_name = r['user_name'].replace('<', '&lt;')
            r_text = r['comment_text'].replace('<', '&lt;')
            text += f"   └ {r_name}: {r_text}\n"
            
        # 如果是展开状态，最后加一个收起按钮
        if show_collapse_btn:
            link = f"https://t.me/{BOT_USERNAME}?start=thread_collapse_{message_id}"
            text += f"   <a href='{link}'>⬆️ 收起</a>\n"
            
    return text


async def handle_channel_interaction(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    message_id = query.message.message_id
    data = query.data.split(':')
    action = data[0]
    
    pool = await get_pool()
    async with pool.acquire() as conn:
        # 获取基本信息 (复用之前的逻辑)
        db_row = await conn.fetchrow("SELECT content_text, user_id, user_name FROM submissions WHERE channel_message_id = $1", message_id)
        if db_row:
            author_link = f'👤 作者: <a href="tg://user?id={db_row["user_id"]}">{db_row["user_name"]}</a>'
            my_link = f'<a href="https://t.me/{BOT_USERNAME}?start=main">📱 我的</a>'
            base_caption = (db_row['content_text'] or "") + f"\n\n━━━━━━━━━━━━━━\n{author_link}  |  {my_link}"
        else:
            base_caption = (query.message.caption_html or "").split("\n\n--- 评论区 ---")[0]

        # 逻辑处理
        show_comments = False
        expanded_comment_id = None # 默认不展开任何子楼层
        
        # 检查当前是否已经是“显示评论”状态
        if "--- 评论区" in (query.message.caption or ""):
            show_comments = True
            
        if action == 'comment':
            sub = data[1]
            if sub == 'show': show_comments = True
            elif sub == 'hide': show_comments = False # 收起整个评论区
            elif sub == 'refresh': show_comments = True # 刷新
        
        # 点赞收藏逻辑 (保持不变)
        elif action in ['react', 'collect']:
            # ... (代码同上一次，此处省略以节省篇幅，逻辑不变) ...
            pass

        # 构建最终文案
        final_caption = base_caption
        if show_comments:
            # 这里调用新写的支持楼中楼的函数
            # 注意：通过按钮点击进来的，默认 expanded_comment_id 为 None
            c_text = await build_threaded_comment_section(conn, message_id, expanded_comment_id=None)
            final_caption += c_text
            
        # 构建按钮 (完全符合你的要求)
        counts = await get_all_counts(conn, message_id)
        
        row1 = [
            InlineKeyboardButton(f"👍 赞 {counts['likes']}", callback_data=f"react:like:{message_id}"),
            InlineKeyboardButton(f"👎 踩 {counts['dislikes']}", callback_data=f"react:dislike:{message_id}"),
            InlineKeyboardButton(f"⭐ 收藏 {counts['collections']}", callback_data=f"collect:{message_id}"),
        ]
        
        row2 = []
        if not show_comments:
            # 未打开评论区 -> 显示 [评论]
            row2.append(InlineKeyboardButton(f"💬 评论 {counts['comments']}", callback_data=f"comment:show:{message_id}"))
        else:
            # 已打开评论区 -> 显示 [发表] [删除] [刷新]
            add_url = f"https://t.me/{BOT_USERNAME}?start=comment_{message_id}" # 发表主评论
            del_url = f"https://t.me/{BOT_USERNAME}?start=manage_comments_{message_id}"
            row2.append(InlineKeyboardButton("✍️ 发表", url=add_url))
            row2.append(InlineKeyboardButton("🗑️ 删除", url=del_url))
            row2.append(InlineKeyboardButton("🔄 刷新", callback_data=f"comment:refresh:{message_id}"))
        
        row3 = []
        if show_comments:
            # 只有在显示评论区时，才显示底部的 [收起]
            row3.append(InlineKeyboardButton("⬆️ 收起", callback_data=f"comment:hide:{message_id}"))

        kb = InlineKeyboardMarkup([row1, row2, row3] if row3 else [row1, row2])
        
        if final_caption != query.message.caption_html or kb != query.message.reply_markup:
            try: await query.edit_message_caption(caption=final_caption, parse_mode=ParseMode.HTML, reply_markup=kb)
            except: pass
