# handlers/submission.py

import math
import logging
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, InputMediaVideo
from telegram.constants import ParseMode
from telegram.ext import ContextTypes, ConversationHandler
from telegram.error import TelegramError

from config import (
    ADMIN_GROUP_ID, 
    GETTING_POST, 
    WAITING_CAPTION,      # 新状态
    CONFIRM_SUBMISSION,   # 新状态
    CHANNEL_USERNAME, 
    CHANNEL_ID,
    CHOOSING, 
    BROWSING_POSTS, 
    BROWSING_COLLECTIONS,
    DELETING_WORK
)
from database import get_pool

logger = logging.getLogger(__name__)

# ================== 数据库与工具函数 (保持不变) ==================

async def delete_post_data(conn, channel_message_id: int):
    """级联删除所有相关数据"""
    await conn.execute("DELETE FROM comments WHERE channel_message_id = $1", channel_message_id)
    await conn.execute("DELETE FROM reactions WHERE channel_message_id = $1", channel_message_id)
    await conn.execute("DELETE FROM collections WHERE channel_message_id = $1", channel_message_id)
    await conn.execute("DELETE FROM pinned_posts WHERE channel_message_id = $1", channel_message_id)
    await conn.execute("DELETE FROM submissions WHERE channel_message_id = $1", channel_message_id)

async def check_channel_post_directly(context: ContextTypes.DEFAULT_TYPE, pool, post):
    """直接尝试在频道内刷新该消息的按钮"""
    msg_id = post['channel_message_id']
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT reaction_type, COUNT(*) as count FROM reactions WHERE channel_message_id = $1 GROUP BY reaction_type", msg_id)
        counts = {row['reaction_type']: row['count'] for row in rows}
        likes = counts.get(1, 0)
        dislikes = counts.get(-1, 0)
        col_count = await conn.fetchval("SELECT COUNT(*) FROM collections WHERE channel_message_id = $1", msg_id) or 0
        com_count = await conn.fetchval("SELECT COUNT(*) FROM comments WHERE channel_message_id = $1", msg_id) or 0
    
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
        await context.bot.edit_message_reply_markup(
            chat_id=CHANNEL_ID,
            message_id=msg_id,
            reply_markup=reply_markup
        )
        return post 
    except TelegramError as e:
        error_str = str(e).lower()
        if "not found" in error_str or "deleted" in error_str or "message_id_invalid" in error_str:
            return None 
        if "message is not modified" in error_str:
            return post
        return post

async def verify_and_clean_posts(context: ContextTypes.DEFAULT_TYPE, raw_posts, pool):
    """批量执行检测"""
    tasks = []
    for post in raw_posts:
        tasks.append(check_channel_post_directly(context, pool, post))
    results = await asyncio.gather(*tasks)
    valid_posts = []
    ids_to_delete = []
    for original_post, result in zip(raw_posts, results):
        if result:
            valid_posts.append(result)
        else:
            ids_to_delete.append(original_post['channel_message_id'])
    if ids_to_delete:
        async with pool.acquire() as conn:
            for mid in ids_to_delete:
                await delete_post_data(conn, mid)
    return valid_posts


# ================== 新版投稿流程 ==================

async def prompt_submission(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """开始投稿"""
    query = update.callback_query
    await query.answer()
    
    # 清理旧数据
    context.user_data.pop('submission_data', None)
    
    await query.edit_message_text(
        "📝 <b>开始投稿</b>\n\n"
        "请发送您的作品（图片、视频或文字）。\n"
        "💡 小提示：您可以直接在图片中附带文案，也可以发完图片后单独发文案。",
        parse_mode=ParseMode.HTML
    )
    return GETTING_POST


async def handle_media_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """阶段1：接收用户发送的媒体"""
    message = update.message
    
    # 暂存消息ID，方便后续复制
    context.user_data['submission_data'] = {
        'message_id': message.message_id,
        'chat_id': message.chat_id,
        'caption': message.caption or message.text or "" # 此时已有的文案
    }

    # 情况 A：已经带了文案，或者是纯文本 -> 直接进确认页
    if message.caption or message.text:
        return await show_confirmation_menu(update, context)
    
    # 情况 B：只有图片/视频，没有文案 -> 询问是否添加
    else:
        keyboard = [
            [InlineKeyboardButton("📝 添加文案", callback_data='add_caption_yes')],
            [InlineKeyboardButton("🚀 直接发送 (无文案)", callback_data='add_caption_no')],
            [InlineKeyboardButton("❌ 取消投稿", callback_data='confirm_cancel')]
        ]
        await message.reply_text(
            "👀 收到图片/视频，但没有附带文案。\n\n"
            "您想要补充一段文字说明吗？",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return WAITING_CAPTION


async def handle_add_caption_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """阶段2：处理按钮选择（添加文案 vs 直接发送）"""
    query = update.callback_query
    await query.answer()
    
    choice = query.data
    
    if choice == 'add_caption_yes':
        await query.edit_message_text("✍️ 好的，请直接回复您想添加的文案内容：")
        return WAITING_CAPTION
        
    elif choice == 'add_caption_no':
        # 用户确认不加文案，直接去确认页
        return await show_confirmation_menu(update, context)


async def handle_caption_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """阶段2.5：接收用户补发的文案"""
    text = update.message.text
    
    # 更新暂存的数据
    if 'submission_data' in context.user_data:
        context.user_data['submission_data']['caption'] = text
        
    await update.message.reply_text("✅ 文案已添加！正在生成预览...")
    return await show_confirmation_menu(update, context)


async def show_confirmation_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """阶段3：展示最终预览并确认"""
    data = context.user_data.get('submission_data')
    if not data:
        msg = update.message or update.callback_query.message
        await msg.reply_text("❌ 数据已过期，请重新投稿。")
        return ConversationHandler.END

    # 这里的技巧：使用 copy_message 把用户最开始发的那个媒体复制回来
    # 但是替换掉它的 caption (如果有新文案)
    
    msg_to_reply = update.message or update.callback_query.message
    chat_id = msg_to_reply.chat_id
    
    preview_caption = f"📄 <b>投稿预览</b>\n\n{data['caption']}\n\n━━━━━━━━━━━━━━\n👆 最终效果如上，确认发布吗？"
    
    keyboard = [
        [InlineKeyboardButton("✅ 确认发布", callback_data='confirm_send')],
        [InlineKeyboardButton("❌ 取消", callback_data='confirm_cancel')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    try:
        # 发送预览
        await context.bot.copy_message(
            chat_id=chat_id,
            from_chat_id=data['chat_id'],
            message_id=data['message_id'],
            caption=preview_caption, # 覆盖原caption，显示预览
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup
        )
    except Exception as e:
        logger.error(f"预览发送失败: {e}")
        await context.bot.send_message(chat_id=chat_id, text="❌ 预览生成失败，请重试。")
        return ConversationHandler.END

    return CONFIRM_SUBMISSION


async def handle_confirm_submission(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """阶段4：最终提交给管理员"""
    query = update.callback_query
    await query.answer()
    
    action = query.data
    
    if action == 'confirm_cancel':
        await query.edit_message_caption("❌ 投稿已取消。")
        context.user_data.pop('submission_data', None)
        return ConversationHandler.END
        
    # 执行发送逻辑
    data = context.user_data.get('submission_data')
    user = query.from_user
    
    # 构建给管理员看的按钮
    # 注意：message_id 先用 0 占位，发过去后无法获取，这里我们主要利用 user_id
    # 实际 message_id 需要等管理员审核完发到频道后才生成
    # 但审核逻辑里 approve_callback_data 依赖原始 message_id，这里稍微复杂点
    # 简单做法：我们直接把带最终文案的消息发给管理员
    
    # 重新构建给管理员的 ID 标记
    # 这里有点特殊：因为 copy_message 生成了新消息，我们得把这个新消息的 ID 传给管理员按钮
    # 但我们不能在这里预知。
    # 解决办法：我们把这个 copy 动作放在这里做。
    
    user_info = f"<b>投稿人:</b> {user.full_name} (@{user.username})\n<b>ID:</b> <code>{user.id}</code>"
    final_caption = data['caption']
    
    try:
        # 1. 复制消息给管理员 (使用最终文案)
        sent_msg = await context.bot.copy_message(
            chat_id=ADMIN_GROUP_ID,
            from_chat_id=data['chat_id'],
            message_id=data['message_id'],
            caption=f"{user_info}\n\n{final_caption}",
            parse_mode=ParseMode.HTML
        )
        
        # 2. 给这条管理员群的消息加上审核按钮
        # 此时 sent_msg.message_id 就是审核通过/拒绝时需要操作的 ID
        approve_btn = f"approve:{user.id}:{sent_msg.message_id}"
        decline_btn = f"decline:{user.id}:{sent_msg.message_id}"
        
        markup = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ 通过", callback_data=approve_btn),
            InlineKeyboardButton("❌ 拒绝", callback_data=decline_btn),
        ]])
        
        await context.bot.edit_message_reply_markup(
            chat_id=ADMIN_GROUP_ID,
            message_id=sent_msg.message_id,
            reply_markup=markup
        )
        
        await query.edit_message_caption("✅ <b>投稿成功！</b>\n\n您的作品已提交审核，请耐心等待。", parse_mode=ParseMode.HTML)
        
    except Exception as e:
        logger.error(f"提交审核失败: {e}")
        await query.edit_message_caption(f"❌ 提交失败: {e}")

    context.user_data.pop('submission_data', None)
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("操作已取消。")
    context.user_data.pop('submission_data', None)
    return ConversationHandler.END


# ================== 我的作品列表 (保持不变) ==================
# 此处为了节省篇幅，省略 navigate_my_posts, verify_and_clean_posts 等代码
# 请保留你上一次修改好的 navigate_my_posts 等所有列表管理代码！
# 它们与投稿流程是独立的，不需要修改。
# 务必把上一次完全修复好的列表代码复制到这里下面。

# ... (请在此处粘贴 navigate_my_posts, verify_and_clean_posts, prompt_delete_work 等所有列表相关代码) ...
# 如果你没有备份，我可以再发一次完整的包含列表功能的代码。

# 为了确保代码完整性，这里我把列表相关的代码也放进去，保持你之前的修复成果：

async def navigate_my_posts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    user_id = query.from_user.id
    try:
        target_page = int(query.data.split(':')[1])
    except:
        target_page = 1
    posts_per_page = 10
    pool = await get_pool()
    async with pool.acquire() as conn:
        total_posts = await conn.fetchval("SELECT COUNT(*) FROM submissions WHERE user_id = $1", user_id)
        if total_posts == 0:
            try:
                await query.answer()
                await query.edit_message_text("您还没有发布过任何作品。", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ 返回主菜单", callback_data='back_to_main')]]))
            except: pass
            return BROWSING_POSTS
        total_pages = math.ceil(total_posts / posts_per_page)
        offset = (target_page - 1) * posts_per_page
        raw_posts = await conn.fetch("SELECT id, content_text, timestamp, channel_message_id FROM submissions WHERE user_id = $1 ORDER BY timestamp DESC LIMIT $2 OFFSET $3", user_id, posts_per_page, offset)

    valid_posts = await verify_and_clean_posts(context, raw_posts, pool)
    try: await query.answer()
    except: pass

    if not valid_posts and target_page > 1 and len(raw_posts) > 0:
         query.data = f"my_posts_page:{target_page - 1}"
         return await navigate_my_posts(update, context)
    if not valid_posts and len(raw_posts) > 0:
        await query.edit_message_text("您的作品列表已更新，当前暂无作品。", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ 返回主菜单", callback_data='back_to_main')]]))
        return BROWSING_POSTS

    response_text = f"📂 <b>我的作品管理</b> (第 {target_page} 页)：\n<i>(系统已自动移除被管理员删除的作品)</i>\n\n"
    for i, post in enumerate(valid_posts):
        content = post['content_text']
        msg_id = post['channel_message_id']
        post_text = (content or "[媒体文件]").strip().replace('<', '&lt;').replace('>', '&gt;')
        if len(post_text) > 20: post_text = post_text[:20] + "..."
        post_url = f"https://t.me/{CHANNEL_USERNAME}/{msg_id}"
        display_idx = (target_page - 1) * posts_per_page + i + 1
        response_text += f"<b>{display_idx}.</b> <a href='{post_url}'>{post_text}</a>\n"

    nav_buttons = []
    if target_page > 1: nav_buttons.append(InlineKeyboardButton("⬅️ 上一页", callback_data=f'my_posts_page:{target_page - 1}'))
    if len(valid_posts) == posts_per_page or (total_pages > target_page): nav_buttons.append(InlineKeyboardButton("下一页 ➡️", callback_data=f'my_posts_page:{target_page + 1}'))
    
    keyboard = [nav_buttons, [InlineKeyboardButton("🗑️ 删除本页作品", callback_data=f'delete_work_prompt:{target_page}')], [InlineKeyboardButton("⬅️ 返回主菜单", callback_data='back_to_main')]]
    await query.edit_message_text(response_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    return BROWSING_POSTS

async def prompt_delete_work(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data['delete_work_page'] = int(query.data.split(':')[1])
    await query.edit_message_text(f"🗑️ <b>删除模式</b>\n\n请回复您要删除的作品序号。\n该作品将从机器人记录和频道中<b>永久删除</b>。\n\n回复 /cancel 取消。", parse_mode=ParseMode.HTML)
    return DELETING_WORK

async def handle_delete_work_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.message.from_user.id
    text = update.message.text.strip()
    if not text.isdigit():
        await update.message.reply_text("❌ 请输入数字序号。")
        return DELETING_WORK
    offset = int(text) - 1
    if offset < 0:
         await update.message.reply_text("❌ 序号无效。")
         return DELETING_WORK

    pool = await get_pool()
    async with pool.acquire() as conn:
        target_post = await conn.fetchrow("SELECT id, channel_message_id, content_text FROM submissions WHERE user_id = $1 ORDER BY timestamp DESC LIMIT 1 OFFSET $2", user_id, offset)
        if not target_post:
            await update.message.reply_text("❌ 找不到该序号对应的作品。")
            return DELETING_WORK 
        channel_msg_id = target_post['channel_message_id']
        content_preview = (target_post['content_text'] or "媒体作品")[:20]

        try:
            telegram_deleted = True
            try: await context.bot.delete_message(chat_id=CHANNEL_ID, message_id=channel_msg_id)
            except TelegramError as e:
                if "not found" in str(e).lower(): logger.info("频道消息已不存在")
                else: telegram_deleted = False
            await delete_post_data(conn, channel_msg_id)
            msg = f"✅ 已删除作品：{content_preview}..."
            await update.message.reply_text(msg)
        except Exception as e:
            logger.error(f"删除过程出错: {e}")
            await update.message.reply_text("❌ 删除时发生系统错误。")

    context.user_data.pop('delete_work_page', None)
    await update.message.reply_text("输入 /start 返回主菜单查看更新后的列表。", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ 返回主菜单", callback_data='back_to_main')]]))
    return ConversationHandler.END

async def show_my_collections(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    target_page = int(query.data.split(':')[1])
    posts_per_page = 10
    pool = await get_pool()
    async with pool.acquire() as conn:
        total_posts = await conn.fetchval("SELECT COUNT(*) FROM collections WHERE user_id = $1", user_id)
        if total_posts == 0:
            await query.edit_message_text("您还没有任何收藏哦。", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ 返回主菜单", callback_data='back_to_main')]]))
            return BROWSING_COLLECTIONS
        total_pages = math.ceil(total_posts / posts_per_page)
        offset = (target_page - 1) * posts_per_page
        posts = await conn.fetch("SELECT s.content_text, s.timestamp, s.channel_message_id FROM collections c JOIN submissions s ON c.channel_message_id = s.channel_message_id WHERE c.user_id = $1 ORDER BY c.timestamp DESC LIMIT $2 OFFSET $3", user_id, posts_per_page, offset)
    
    response_text = f"⭐ <b>我的收藏</b> (第 {target_page}/{total_pages} 页)：\n\n"
    for i, post in enumerate(posts):
        content, timestamp, msg_id = post
        post_text = (content or "[媒体文件]").strip().replace('<', '&lt;').replace('>', '&gt;')
        if len(post_text) > 20: post_text = post_text[:20] + "..."
        post_url = f"https://t.me/{CHANNEL_USERNAME}/{msg_id}"
        response_text += f"{offset + i + 1}. <a href='{post_url}'>{post_text}</a>\n"
    
    nav_buttons = []
    if target_page > 1: nav_buttons.append(InlineKeyboardButton("⬅️ 上一页", callback_data=f'my_collections_page:{target_page - 1}'))
    if target_page < total_pages: nav_buttons.append(InlineKeyboardButton("下一页 ➡️", callback_data=f'my_collections_page:{target_page + 1}'))
    
    keyboard = [nav_buttons, [InlineKeyboardButton("⬅️ 返回主菜单", callback_data='back_to_main')]]
    await query.edit_message_text(response_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    return BROWSING_COLLECTIONS
