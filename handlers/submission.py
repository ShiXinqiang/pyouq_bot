# handlers/submission.py

import math
import logging
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import ContextTypes, ConversationHandler
from telegram.error import TelegramError

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


# ================== 核心工具函数：数据库级联删除 ==================

async def delete_post_data(conn, channel_message_id: int):
    """
    从所有相关表中删除指定帖子的数据
    """
    # 删除相关评论
    await conn.execute("DELETE FROM comments WHERE channel_message_id = $1", channel_message_id)
    # 删除相关互动
    await conn.execute("DELETE FROM reactions WHERE channel_message_id = $1", channel_message_id)
    # 删除收藏
    await conn.execute("DELETE FROM collections WHERE channel_message_id = $1", channel_message_id)
    # 删除置顶记录
    await conn.execute("DELETE FROM pinned_posts WHERE channel_message_id = $1", channel_message_id)
    # 最后删除投稿记录
    await conn.execute("DELETE FROM submissions WHERE channel_message_id = $1", channel_message_id)


# ================== 核心工具函数：验证消息是否存在 ==================

async def verify_and_clean_posts(context: ContextTypes.DEFAULT_TYPE, posts, pool):
    """
    并发验证帖子在频道中是否存在，不存在则从数据库删除
    返回: 仍然存在的帖子列表
    """
    valid_posts = []
    tasks = []

    # 定义单个检查任务
    async def check_single_post(post):
        msg_id = post['channel_message_id']
        try:
            # 尝试转发消息到审核群（静音），如果成功说明消息存在
            # 这是一个轻量级的检测方法
            sent = await context.bot.forward_message(
                chat_id=ADMIN_GROUP_ID,
                from_chat_id=CHANNEL_ID,
                message_id=msg_id,
                disable_notification=True
            )
            # 立即删除转发产生的消息，保持审核群整洁
            await context.bot.delete_message(chat_id=ADMIN_GROUP_ID, message_id=sent.message_id)
            return post # 存在
        except TelegramError as e:
            # 如果错误包含 not found 或 deleted，说明原消息已不在
            error_str = str(e).lower()
            if "not found" in error_str or "deleted" in error_str or "request" in error_str:
                return None # 不存在
            # 其他网络错误等，暂时当作存在处理，以免误删
            return post

    # 创建并发任务
    for post in posts:
        tasks.append(check_single_post(post))

    # 等待所有检查完成
    results = await asyncio.gather(*tasks)
    
    # 收集需要从数据库删除的 ID
    ids_to_delete = []
    for original_post, result in zip(posts, results):
        if result:
            valid_posts.append(result)
        else:
            ids_to_delete.append(original_post['channel_message_id'])
    
    # 批量执行数据库清理
    if ids_to_delete:
        async with pool.acquire() as conn:
            for mid in ids_to_delete:
                await delete_post_data(conn, mid)
        logger.info(f"♻️ 自动同步：已从数据库清理 {len(ids_to_delete)} 条已被管理员删除的帖子。")

    return valid_posts


# ================== 投稿流程 ==================

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


# ================== 我的作品列表 (含自动同步) ==================

async def navigate_my_posts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """查询并展示'我的作品'分页记录"""
    query = update.callback_query
    
    # 稍微延迟 answer，因为我们要进行网络检测，可能需要 1-2 秒
    # await query.answer() 
    
    user_id = query.from_user.id

    # 解析页码
    try:
        data_parts = query.data.split(':')
        target_page = int(data_parts[1])
    except:
        target_page = 1
    
    posts_per_page = 10

    pool = await get_pool()
    async with pool.acquire() as conn:
        # 1. 先获取总数
        total_posts = await conn.fetchval(
            "SELECT COUNT(*) FROM submissions WHERE user_id = $1", 
            user_id
        )
        
        if total_posts == 0:
            await query.answer()
            await query.edit_message_text(
                "您还没有发布过任何作品。",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ 返回主菜单", callback_data='back_to_main')]])
            )
            return BROWSING_POSTS

        total_pages = math.ceil(total_posts / posts_per_page)
        offset = (target_page - 1) * posts_per_page
        
        # 2. 获取当前页的数据库记录
        raw_posts = await conn.fetch(
            "SELECT id, content_text, timestamp, channel_message_id FROM submissions WHERE user_id = $1 ORDER BY timestamp DESC LIMIT $2 OFFSET $3",
            user_id, posts_per_page, offset
        )

    # 3. 【关键步骤】执行同步检查
    # 这会过滤掉那些在频道里已经被删除的帖子
    valid_posts = await verify_and_clean_posts(context, raw_posts, pool)
    
    await query.answer() # 检查完再响应

    # 如果检查后发现这一页空了（都被删了），且不是第一页，自动跳转回上一页或刷新
    if not valid_posts and target_page > 1 and len(raw_posts) > 0:
         # 递归调用自己，去上一页
         query.data = f"my_posts_page:{target_page - 1}"
         return await navigate_my_posts(update, context)
    
    # 如果所有作品都被删光了
    if not valid_posts and len(raw_posts) > 0:
        await query.edit_message_text(
            "您的作品列表已更新，当前暂无作品。",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ 返回主菜单", callback_data='back_to_main')]])
        )
        return BROWSING_POSTS

    # 4. 构建显示文本
    response_text = f"📂 <b>我的作品管理</b> (第 {target_page} 页)：\n"
    response_text += "<i>(系统已自动移除被管理员删除的作品)</i>\n\n"
    
    for i, post in enumerate(valid_posts):
        content = post['content_text']
        msg_id = post['channel_message_id']
        post_text = (content or "[媒体文件]").strip().replace('<', '&lt;').replace('>', '&gt;')
        if len(post_text) > 20: 
            post_text = post_text[:20] + "..."
        
        post_url = f"https://t.me/{CHANNEL_USERNAME}/{msg_id}"
        # 序号逻辑：(页码-1)*10 + 当前索引 + 1
        display_idx = (target_page - 1) * posts_per_page + i + 1
        response_text += f"<b>{display_idx}.</b> <a href='{post_url}'>{post_text}</a>\n"

    # 5. 构建按钮
    nav_buttons = []
    if target_page > 1:
        nav_buttons.append(InlineKeyboardButton("⬅️ 上一页", callback_data=f'my_posts_page:{target_page - 1}'))
    
    # 只有当原始查询数量等于每页数量时，才认为可能还有下一页
    # (注意：因为刚刚可能删除了几个，导致 valid_posts 变少，这里用 raw_posts 判断更准，或者简单处理显示下一页，如果没有下一页用户点击会看到空)
    if total_pages > target_page:
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
    """提示用户输入要删除的序号"""
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
    """处理用户输入的序号并执行删除"""
    user_id = update.message.from_user.id
    text = update.message.text.strip()
    
    page = context.user_data.get('delete_work_page', 1)
    posts_per_page = 10
    
    if not text.isdigit():
        await update.message.reply_text("❌ 请输入数字序号。")
        return DELETING_WORK
        
    input_num = int(text)
    
    # 转换为 SQL 偏移量
    # 比如第2页第1个，input_num 是 11。 offset 应该 是 10 (LIMIT 1 OFFSET 10)
    # 所以 offset = input_num - 1
    offset = input_num - 1
    
    if offset < 0:
         await update.message.reply_text("❌ 序号无效。")
         return DELETING_WORK

    pool = await get_pool()
    async with pool.acquire() as conn:
        # 获取该用户按时间倒序排列的第 N 个帖子
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
            return DELETING_WORK # 保持在删除模式
            
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
            except Exception as e:
                logger.warning(f"从频道删除消息失败 (可能是已被管理员删除): {e}")
                telegram_deleted = False
            
            # 从数据库删除 (复用工具函数)
            await delete_post_data(conn, channel_msg_id)
            
            msg = f"✅ 已删除作品：{content_preview}..."
            if not telegram_deleted:
                msg += "\n(提示：频道中的消息可能已被管理员删除，数据库已同步清理)"
            
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


# ================== 收藏列表 ==================

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
    
    # 收藏列表不需要强制同步删除检测，因为收藏的是历史
    # 但如果为了体验好，也可以加上 verify_and_clean_posts，这里暂时保持原样，只显示
    
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
