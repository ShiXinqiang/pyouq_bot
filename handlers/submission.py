# handlers/submission.py

import math
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import ContextTypes, ConversationHandler

from config import (
    ADMIN_GROUP_ID, 
    GETTING_POST, 
    CHANNEL_USERNAME, 
    CHANNEL_ID,
    CHOOSING, 
    BROWSING_POSTS, 
    BROWSING_COLLECTIONS,
    DELETING_WORK # 引入新状态
)
from database import get_pool

logger = logging.getLogger(__name__)

async def prompt_submission(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """提示用户发送要投稿的内容"""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "好的，请发送您的作品（文字、图片、视频等）。\n\n"
        "随时可以输入 /cancel 取消操作。"
    )
    return GETTING_POST


async def handle_new_post(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """处理用户发送的投稿"""
    message = update.message
    user = message.from_user

    approve_callback_data = f"approve:{user.id}:{message.message_id}"
    decline_callback_data = f"decline:{user.id}:{message.message_id}"
    keyboard = [[
        InlineKeyboardButton("✅ 通过", callback_data=approve_callback_data),
        InlineKeyboardButton("❌ 拒绝", callback_data=decline_callback_data),
    ]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    user_info = f"<b>投稿人:</b> {user.full_name} (@{user.username})\n<b>ID:</b> <code>{user.id}</code>"

    try:
        await context.bot.copy_message(
            chat_id=ADMIN_GROUP_ID,
            from_chat_id=user.id,
            message_id=message.id,
            caption=f"{user_info}\n\n{message.caption or ''}",
            reply_markup=reply_markup,
            parse_mode=ParseMode.HTML
        )
        await message.reply_text("✅ 您的作品已提交审核，请耐心等待。")
    except Exception as e:
        await message.reply_text(f"❌ 提交失败: {e}")

    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("操作已取消。")
    return ConversationHandler.END


# ================== 修改：我的作品列表 ==================

async def navigate_my_posts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """查询并展示'我的作品'分页记录"""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    # 解析页码
    data_parts = query.data.split(':')
    target_page = int(data_parts[1])
    
    posts_per_page = 10

    pool = await get_pool()
    async with pool.acquire() as conn:
        total_posts = await conn.fetchval(
            "SELECT COUNT(*) FROM submissions WHERE user_id = $1", 
            user_id
        )
        
        if total_posts == 0:
            await query.edit_message_text(
                "您还没有发布过任何作品。",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ 返回主菜单", callback_data='back_to_main')]])
            )
            return BROWSING_POSTS

        total_pages = math.ceil(total_posts / posts_per_page)
        offset = (target_page - 1) * posts_per_page
        
        posts = await conn.fetch(
            "SELECT content_text, timestamp, channel_message_id FROM submissions WHERE user_id = $1 ORDER BY timestamp DESC LIMIT $2 OFFSET $3",
            user_id, posts_per_page, offset
        )

    response_text = f"📂 <b>我的作品管理</b> (第 {target_page}/{total_pages} 页)：\n\n"
    for i, post in enumerate(posts):
        content, timestamp, msg_id = post
        post_text = (content or "[媒体文件]").strip().replace('<', '&lt;').replace('>', '&gt;')
        if len(post_text) > 20: 
            post_text = post_text[:20] + "..."
        
        post_url = f"https://t.me/{CHANNEL_USERNAME}/{msg_id}"
        response_text += f"<b>{i + 1}.</b> <a href='{post_url}'>{post_text}</a>\n"

    # 构建按钮
    nav_buttons = []
    if target_page > 1:
        nav_buttons.append(InlineKeyboardButton("⬅️ 上一页", callback_data=f'my_posts_page:{target_page - 1}'))
    if target_page < total_pages:
        nav_buttons.append(InlineKeyboardButton("下一页 ➡️", callback_data=f'my_posts_page:{target_page + 1}'))
    
    keyboard = [
        nav_buttons,
        # 新增：删除按钮，传递当前页码
        [InlineKeyboardButton("🗑️ 删除本页作品", callback_data=f'delete_work_prompt:{target_page}')],
        [InlineKeyboardButton("⬅️ 返回主菜单", callback_data='back_to_main')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        response_text,
        reply_markup=reply_markup,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True
    )
    
    return BROWSING_POSTS


# ================== 新增：删除作品逻辑 ==================

async def prompt_delete_work(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """提示用户输入要删除的序号"""
    query = update.callback_query
    await query.answer()
    
    # 获取当前页码
    current_page = int(query.data.split(':')[1])
    context.user_data['delete_work_page'] = current_page
    
    await query.edit_message_text(
        f"🗑️ <b>删除模式</b>\n\n"
        f"请回复您要删除的作品序号（1-10）。\n"
        f"该作品将从机器人记录和频道中<b>永久删除</b>。\n\n"
        f"回复 /cancel 取消。",
        parse_mode=ParseMode.HTML
    )
    return DELETING_WORK


async def handle_delete_work_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """处理用户输入的序号并执行删除"""
    user_id = update.message.from_user.id
    text = update.message.text.strip()
    
    page = context.user_data.get('delete_work_page', 1)
    posts_per_page = 10
    
    if not text.isdigit():
        await update.message.reply_text("❌ 请输入数字序号。")
        return DELETING_WORK
        
    index = int(text) - 1 # 转换为 0-based索引
    
    if index < 0 or index >= posts_per_page:
         await update.message.reply_text("❌ 序号无效，请输入 1-10 之间的数字。")
         return DELETING_WORK

    pool = await get_pool()
    async with pool.acquire() as conn:
        # 1. 找到对应的帖子 ID
        offset = (page - 1) * posts_per_page
        
        # 获取该用户按时间倒序排列的第 N 个帖子
        # 注意：这里必须和列表显示的排序逻辑完全一致
        target_post = await conn.fetchrow(
            """
            SELECT id, channel_message_id, content_text 
            FROM submissions 
            WHERE user_id = $1 
            ORDER BY timestamp DESC 
            LIMIT 1 OFFSET $2
            """,
            user_id, offset + index
        )
        
        if not target_post:
            await update.message.reply_text("❌ 找不到该作品，可能已被删除或序号错误。")
            return ConversationHandler.END # 或者回到列表
            
        submission_id = target_post['id']
        channel_msg_id = target_post['channel_message_id']
        content_preview = (target_post['content_text'] or "媒体作品")[:20]

        try:
            # 2. 从 Telegram 频道撤回消息
            try:
                await context.bot.delete_message(
                    chat_id=CHANNEL_ID,
                    message_id=channel_msg_id
                )
                telegram_deleted = True
            except Exception as e:
                logger.warning(f"从频道删除消息失败 (可能是消息太久远): {e}")
                telegram_deleted = False
            
            # 3. 从数据库级联删除
            # 删除相关评论
            await conn.execute("DELETE FROM comments WHERE channel_message_id = $1", channel_msg_id)
            # 删除相关互动
            await conn.execute("DELETE FROM reactions WHERE channel_message_id = $1", channel_msg_id)
            # 删除收藏
            await conn.execute("DELETE FROM collections WHERE channel_message_id = $1", channel_msg_id)
            # 删除置顶记录
            await conn.execute("DELETE FROM pinned_posts WHERE channel_message_id = $1", channel_msg_id)
            # 最后删除投稿记录
            await conn.execute("DELETE FROM submissions WHERE id = $1", submission_id)
            
            msg = f"✅ 已删除作品：{content_preview}..."
            if not telegram_deleted:
                msg += "\n(注意：频道消息可能因时间过久无法自动撤回，请联系管理员手动处理)"
            
            await update.message.reply_text(msg)
            
        except Exception as e:
            logger.error(f"删除过程出错: {e}")
            await update.message.reply_text("❌ 删除时发生系统错误。")

    # 删除完成后，清理状态，重置回主菜单或列表
    context.user_data.pop('delete_work_page', None)
    
    # 稍微引导一下用户
    await update.message.reply_text(
        "输入 /start 返回主菜单查看更新后的列表。",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ 返回主菜单", callback_data='back_to_main')]])
    )
    
    return ConversationHandler.END


# ================== 收藏列表逻辑 (仅修改文案) ==================

async def show_my_collections(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """查询并展示'我的收藏'分页记录"""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    target_page = int(query.data.split(':')[1])
    posts_per_page = 10

    pool = await get_pool()
    async with pool.acquire() as conn:
        total_posts = await conn.fetchval(
            "SELECT COUNT(*) FROM collections WHERE user_id = $1", 
            user_id
        )

        if total_posts == 0:
            await query.edit_message_text(
                "您还没有任何收藏哦。",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ 返回主菜单", callback_data='back_to_main')]])
            )
            return BROWSING_COLLECTIONS

        total_pages = math.ceil(total_posts / posts_per_page)
        offset = (target_page - 1) * posts_per_page

        posts = await conn.fetch(
            """
            SELECT s.content_text, s.timestamp, s.channel_message_id
            FROM collections c JOIN submissions s ON c.channel_message_id = s.channel_message_id
            WHERE c.user_id = $1 ORDER BY c.timestamp DESC LIMIT $2 OFFSET $3
            """,
            user_id, posts_per_page, offset
        )
    
    response_text = f"⭐ <b>我的收藏</b> (第 {target_page}/{total_pages} 页)：\n\n"
    for i, post in enumerate(posts):
        content, timestamp, msg_id = post
        post_text = (content or "[媒体文件]").strip().replace('<', '&lt;').replace('>', '&gt;')
        if len(post_text) > 20: 
            post_text = post_text[:20] + "..."
        
        post_url = f"https://t.me/{CHANNEL_USERNAME}/{msg_id}"
        response_text += f"{offset + i + 1}. <a href='{post_url}'>{post_text}</a>\n"
    
    nav_buttons = []
    if target_page > 1:
        nav_buttons.append(InlineKeyboardButton("⬅️ 上一页", callback_data=f'my_collections_page:{target_page - 1}'))
    if target_page < total_pages:
        nav_buttons.append(InlineKeyboardButton("下一页 ➡️", callback_data=f'my_collections_page:{target_page + 1}'))
    
    keyboard = [
        nav_buttons,
        [InlineKeyboardButton("⬅️ 返回主菜单", callback_data='back_to_main')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        response_text,
        reply_markup=reply_markup,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True
    )

    return BROWSING_COLLECTIONS
