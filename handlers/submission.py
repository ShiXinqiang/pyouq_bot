# handlers/submission.py

import math
import logging
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import ContextTypes, ConversationHandler
from telegram.error import TelegramError, BadRequest

from config import (
    ADMIN_GROUP_ID, 
    GETTING_POST, 
    CHANNEL_USERNAME, 
    CHANNEL_ID,
    CHOOSING, 
    BROWSING_POSTS, 
    BROWSING_COLLECTIONS,
    DELETING_WORK
)
from database import get_pool

logger = logging.getLogger(__name__)


# ================== 核心工具：数据库清理 ==================

async def delete_post_data(conn, channel_message_id: int):
    """级联删除所有相关数据"""
    await conn.execute("DELETE FROM comments WHERE channel_message_id = $1", channel_message_id)
    await conn.execute("DELETE FROM reactions WHERE channel_message_id = $1", channel_message_id)
    await conn.execute("DELETE FROM collections WHERE channel_message_id = $1", channel_message_id)
    await conn.execute("DELETE FROM pinned_posts WHERE channel_message_id = $1", channel_message_id)
    await conn.execute("DELETE FROM submissions WHERE channel_message_id = $1", channel_message_id)


# ================== 核心工具：直接在频道检测 (修复并发问题版) ==================

async def check_channel_post_directly(context: ContextTypes.DEFAULT_TYPE, pool, post):
    """
    直接尝试在频道内刷新该消息的按钮。
    修复：接收 pool 而不是 conn，每个任务独立获取连接，避免 InterfaceError。
    """
    msg_id = post['channel_message_id']
    
    # 1. 获取最新的互动数据
    # 使用独立的连接上下文，用完即还，避免并发冲突
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT reaction_type, COUNT(*) as count FROM reactions WHERE channel_message_id = $1 GROUP BY reaction_type", msg_id)
        counts = {row['reaction_type']: row['count'] for row in rows}
        likes = counts.get(1, 0)
        dislikes = counts.get(-1, 0)
        
        col_count = await conn.fetchval("SELECT COUNT(*) FROM collections WHERE channel_message_id = $1", msg_id) or 0
        com_count = await conn.fetchval("SELECT COUNT(*) FROM comments WHERE channel_message_id = $1", msg_id) or 0
    
    # 2. 构建键盘
    keyboard = [
        [
            InlineKeyboardButton(f"👍 赞 {likes}", callback_data=f"react:like:{msg_id}"),
            InlineKeyboardButton(f"👎 踩 {dislikes}", callback_data=f"react:dislike:{msg_id}"),
            InlineKeyboardButton(f"⭐ 收藏 {col_count}", callback_data=f"collect:{msg_id}"),
        ],
        [
            InlineKeyboardButton(f"💬 评论 {com_count}", callback_data=f"comment:show:{msg_id}"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    try:
        # 3. 尝试编辑频道消息的按钮
        # 这一步不需要数据库连接，是纯网络请求
        await context.bot.edit_message_reply_markup(
            chat_id=CHANNEL_ID,
            message_id=msg_id,
            reply_markup=reply_markup
        )
        return post # 消息存在
        
    except TelegramError as e:
        error_str = str(e).lower()
        
        # 4. 判定逻辑
        # 包括 "message to edit not found", "message not found", 以及 "message_id_invalid"
        if "not found" in error_str or "deleted" in error_str or "message_id_invalid" in error_str:
            logger.info(f"🗑️ [直接检测] 频道消息 {msg_id} 已失效 ({error_str})，标记为删除...")
            return None # 标记删除
            
        # 如果是 "message is not modified"，说明消息存在
        if "message is not modified" in error_str:
            return post
            
        logger.warning(f"⚠️ 检测消息 {msg_id} 时遇到意外错误: {e}")
        return post


async def verify_and_clean_posts(context: ContextTypes.DEFAULT_TYPE, raw_posts, pool):
    """
    批量执行检测 (并发安全版)
    """
    tasks = []
    # 这里不要 acquire conn，而是把 pool 传给子任务
    for post in raw_posts:
        tasks.append(check_channel_post_directly(context, pool, post))
    
    # 并发执行所有检测
    results = await asyncio.gather(*tasks)
    
    valid_posts = []
    ids_to_delete = []
    
    # 整理结果
    for original_post, result in zip(raw_posts, results):
        if result:
            valid_posts.append(result)
        else:
            ids_to_delete.append(original_post['channel_message_id'])
    
    # 统一删除失效数据
    if ids_to_delete:
        # 这里单独获取一个连接来执行删除操作
        async with pool.acquire() as conn:
            for mid in ids_to_delete:
                await delete_post_data(conn, mid)
        logger.info(f"♻️ 已清理 {len(ids_to_delete)} 条无效作品。")

    return valid_posts


# ================== 投稿流程 (保持不变) ==================

async def prompt_submission(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "好的，请发送您的作品（文字、图片、视频等）。\n\n"
        "随时可以输入 /cancel 取消操作。"
    )
    return GETTING_POST


async def handle_new_post(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
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


# ================== 我的作品列表 (逻辑更新) ==================

async def navigate_my_posts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    user_id = query.from_user.id

    try:
        data_parts = query.data.split(':')
        target_page = int(data_parts[1])
    except:
        target_page = 1
    
    posts_per_page = 10

    pool = await get_pool()
    # 1. 获取数据的连接
    async with pool.acquire() as conn:
        total_posts = await conn.fetchval(
            "SELECT COUNT(*) FROM submissions WHERE user_id = $1", 
            user_id
        )
        
        if total_posts == 0:
            try:
                await query.answer()
                await query.edit_message_text(
                    "您还没有发布过任何作品。",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ 返回主菜单", callback_data='back_to_main')]])
                )
            except:
                pass
            return BROWSING_POSTS

        total_pages = math.ceil(total_posts / posts_per_page)
        offset = (target_page - 1) * posts_per_page
        
        raw_posts = await conn.fetch(
            "SELECT id, content_text, timestamp, channel_message_id FROM submissions WHERE user_id = $1 ORDER BY timestamp DESC LIMIT $2 OFFSET $3",
            user_id, posts_per_page, offset
        )

    # 2. 【关键修改】执行检测 (传入 pool，不传入 conn)
    valid_posts = await verify_and_clean_posts(context, raw_posts, pool)
    
    try:
        await query.answer()
    except:
        pass

    # 递归处理空页
    if not valid_posts and target_page > 1 and len(raw_posts) > 0:
         query.data = f"my_posts_page:{target_page - 1}"
         return await navigate_my_posts(update, context)
    
    if not valid_posts and len(raw_posts) > 0:
        await query.edit_message_text(
            "您的作品列表已更新，当前暂无作品。",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ 返回主菜单", callback_data='back_to_main')]])
        )
        return BROWSING_POSTS

    response_text = f"📂 <b>我的作品管理</b> (第 {target_page} 页)：\n"
    response_text += "<i>(系统已自动移除被管理员删除的作品)</i>\n\n"
    
    for i, post in enumerate(valid_posts):
        content = post['content_text']
        msg_id = post['channel_message_id']
        post_text = (content or "[媒体文件]").strip().replace('<', '&lt;').replace('>', '&gt;')
        if len(post_text) > 20: 
            post_text = post_text[:20] + "..."
        
        post_url = f"https://t.me/{CHANNEL_USERNAME}/{msg_id}"
        display_idx = (target_page - 1) * posts_per_page + i + 1
        response_text += f"<b>{display_idx}.</b> <a href='{post_url}'>{post_text}</a>\n"

    nav_buttons = []
    if target_page > 1:
        nav_buttons.append(InlineKeyboardButton("⬅️ 上一页", callback_data=f'my_posts_page:{target_page - 1}'))
    
    if len(valid_posts) == posts_per_page or (total_pages > target_page):
        nav_buttons.append(InlineKeyboardButton("下一页 ➡️", callback_data=f'my_posts_page:{target_page + 1}'))
    
    keyboard = [
        nav_buttons,
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


# ================== 手动删除作品逻辑 ==================

async def prompt_delete_work(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    
    current_page = int(query.data.split(':')[1])
    context.user_data['delete_work_page'] = current_page
    
    await query.edit_message_text(
        f"🗑️ <b>删除模式</b>\n\n"
        f"请回复您要删除的作品序号。\n"
        f"该作品将从机器人记录和频道中<b>永久删除</b>。\n\n"
        f"回复 /cancel 取消。",
        parse_mode=ParseMode.HTML
    )
    return DELETING_WORK


async def handle_delete_work_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.message.from_user.id
    text = update.message.text.strip()
    
    if not text.isdigit():
        await update.message.reply_text("❌ 请输入数字序号。")
        return DELETING_WORK
        
    input_num = int(text)
    offset = input_num - 1
    
    if offset < 0:
         await update.message.reply_text("❌ 序号无效。")
         return DELETING_WORK

    pool = await get_pool()
    async with pool.acquire() as conn:
        target_post = await conn.fetchrow(
            """
            SELECT id, channel_message_id, content_text 
            FROM submissions 
            WHERE user_id = $1 
            ORDER BY timestamp DESC 
            LIMIT 1 OFFSET $2
            """,
            user_id, offset
        )
        
        if not target_post:
            await update.message.reply_text("❌ 找不到该序号对应的作品，请检查序号是否正确。")
            return DELETING_WORK 
            
        channel_msg_id = target_post['channel_message_id']
        content_preview = (target_post['content_text'] or "媒体作品")[:20]

        try:
            telegram_deleted = True
            try:
                # 尝试从频道撤回
                await context.bot.delete_message(
                    chat_id=CHANNEL_ID,
                    message_id=channel_msg_id
                )
            except TelegramError as e:
                # 如果错误是 "message not found"，说明已经被管理员删了，不报错，继续删库
                if "not found" in str(e).lower():
                    logger.info("频道消息已不存在，跳过 Telegram 删除步骤")
                else:
                    logger.warning(f"从频道删除消息失败: {e}")
                    telegram_deleted = False
            
            await delete_post_data(conn, channel_msg_id)
            
            msg = f"✅ 已删除作品：{content_preview}..."
            await update.message.reply_text(msg)
            
        except Exception as e:
            logger.error(f"删除过程出错: {e}")
            await update.message.reply_text("❌ 删除时发生系统错误。")

    context.user_data.pop('delete_work_page', None)
    
    await update.message.reply_text(
        "输入 /start 返回主菜单查看更新后的列表。",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ 返回主菜单", callback_data='back_to_main')]])
    )
    
    return ConversationHandler.END


# ================== 收藏列表 (无需变动) ==================

async def show_my_collections(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
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
