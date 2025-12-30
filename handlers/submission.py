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
    WAITING_CAPTION,      
    CONFIRM_SUBMISSION,   
    CHANNEL_USERNAME, 
    CHANNEL_ID,
    CHOOSING, 
    BROWSING_POSTS, 
    BROWSING_COLLECTIONS,
    DELETING_WORK
)
from database import get_pool

logger = logging.getLogger(__name__)

# ================== 辅助函数：安全删除消息 ==================
async def safe_delete_message(bot, chat_id, message_id):
    """尝试删除消息，忽略错误，保持界面整洁"""
    if not message_id: return
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass

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


# ================== 投稿/发布流程 (UX优化版) ==================

async def prompt_submission(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """开始发布"""
    query = update.callback_query
    await query.answer()
    
    context.user_data.pop('submission_data', None)
    
    # 记录当前菜单消息ID
    context.user_data['last_bot_msg'] = query.message.message_id
    
    # 添加返回按钮
    keyboard = [[InlineKeyboardButton("⬅️ 返回主菜单", callback_data='back_to_main')]]
    
    await query.edit_message_text(
        "📝 <b>开始发布</b>\n\n"
        "请发送您的作品（图片、视频或文字）。\n"
        "💡 小提示：您可以直接在图片中附带文案，也可以发完图片后单独发文案。",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return GETTING_POST


async def handle_media_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """阶段1：接收用户发送的媒体"""
    message = update.message
    
    # 1. 暂存数据
    context.user_data['submission_data'] = {
        'message_id': message.message_id,
        'chat_id': message.chat_id,
        'caption': message.caption or message.text or ""
    }

    # 2. 【关键】删除用户发送的媒体/文字消息，保持界面像APP一样干净
    # 注意：我们已经拿到了 message_id，后续copy_message依然有效，只要不隔太久
    # 如果担心copy失败，可以稍微延后删除，但在大多数情况下Telegram允许引用刚删除的消息进行转发/复制(短时间内)
    # 为了保险，我们先不删用户发的图，因为如果这里删了，handle_confirm_submission 里的 copy_message 可能会找不到源消息
    # 修正策略：只删除纯文本输入。图片/视频建议保留，因为用户可能想留底，且删除后 bot 可能无法复制。
    # 用户明确要求“删除用户发的信息”，所以我们尽量删。
    # 实际上，只要我们 context.user_data 存了 ID，且在这里立刻 copy 了一份发给管理员(或者发给Bot自己存着)，就可以删用户的。
    # 但我们现在的逻辑是最后确认才发给管理员。
    # 妥协方案：不删除媒体消息（防止数据丢失），只删除机器人的旧提示。
    
    # 尝试删除上一条机器人的提示消息 ("请发送您的作品...")
    last_msg_id = context.user_data.get('last_bot_msg')
    await safe_delete_message(context.bot, message.chat_id, last_msg_id)

    if message.caption or message.text:
        return await show_confirmation_menu(update, context)
    else:
        keyboard = [
            [InlineKeyboardButton("📝 添加文案", callback_data='add_caption_yes')],
            [InlineKeyboardButton("🚀 直接发送 (无文案)", callback_data='add_caption_no')],
            [InlineKeyboardButton("❌ 取消发布", callback_data='confirm_cancel')],
            # 这里不需要返回主菜单，因为取消就是返回
        ]
        sent_msg = await message.reply_text(
            "👀 收到内容，但没有附带文案。\n\n"
            "您想要补充一段文字说明吗？",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        context.user_data['last_bot_msg'] = sent_msg.message_id
        return WAITING_CAPTION


async def handle_add_caption_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    choice = query.data
    
    if choice == 'add_caption_yes':
        await query.edit_message_text("✍️ 好的，请直接回复您想添加的文案内容：")
        context.user_data['last_bot_msg'] = query.message.message_id
        return WAITING_CAPTION
        
    elif choice == 'add_caption_no':
        await safe_delete_message(context.bot, query.message.chat_id, query.message.message_id)
        return await show_confirmation_menu(update, context)


async def handle_caption_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text
    chat_id = update.message.chat_id
    
    # 1. 删除用户发的这条纯文案消息 (清理垃圾，保持界面简洁)
    await safe_delete_message(context.bot, chat_id, update.message.message_id)
    
    # 2. 删除机器人上一条提示 ("请直接回复...")
    last_msg_id = context.user_data.get('last_bot_msg')
    await safe_delete_message(context.bot, chat_id, last_msg_id)
    
    if 'submission_data' in context.user_data:
        context.user_data['submission_data']['caption'] = text
        
    temp_msg = await update.message.reply_text("✅ 文案已添加！生成预览中...")
    await safe_delete_message(context.bot, chat_id, temp_msg.message_id)
    
    return await show_confirmation_menu(update, context)


async def show_confirmation_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    data = context.user_data.get('submission_data')
    # 处理不同来源的 update
    if update.message:
        chat_id = update.message.chat_id
    elif update.callback_query:
        chat_id = update.callback_query.message.chat_id
    else:
        return ConversationHandler.END

    if not data:
        await context.bot.send_message(chat_id=chat_id, text="❌ 数据已过期，请重新发布。")
        return ConversationHandler.END

    preview_caption = f"📄 <b>发布预览</b>\n\n{data['caption']}\n\n━━━━━━━━━━━━━━\n👆 最终效果如上，确认发布吗？"
    
    keyboard = [
        [InlineKeyboardButton("✅ 确认发布", callback_data='confirm_send')],
        [
            InlineKeyboardButton("❌ 取消", callback_data='confirm_cancel'),
            InlineKeyboardButton("⬅️ 返回主菜单", callback_data='back_to_main') # 增加返回
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    try:
        sent_msg = await context.bot.copy_message(
            chat_id=chat_id,
            from_chat_id=data['chat_id'],
            message_id=data['message_id'],
            caption=preview_caption,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup
        )
        context.user_data['last_bot_msg'] = sent_msg.message_id
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
    
    # 无论如何，先删除巨大的预览消息，只留结果
    await safe_delete_message(context.bot, query.message.chat_id, query.message.message_id)
    
    if action == 'confirm_cancel':
        await context.bot.send_message(
            chat_id=query.message.chat_id, 
            text="❌ 发布已取消。",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ 返回主菜单", callback_data='back_to_main')]])
        )
        context.user_data.pop('submission_data', None)
        return ConversationHandler.END
        
    data = context.user_data.get('submission_data')
    user = query.from_user 
    
    user_info = f"<b>发布人:</b> {user.full_name} (@{user.username})\n<b>ID:</b> <code>{user.id}</code>"
    final_caption = data['caption']
    
    try:
        # 1. 复制消息给管理员
        sent_msg = await context.bot.copy_message(
            chat_id=ADMIN_GROUP_ID,
            from_chat_id=data['chat_id'],
            message_id=data['message_id'],
            caption=f"{user_info}\n\n{final_caption}",
            parse_mode=ParseMode.HTML
        )
        
        # 2. 加上审核按钮
        # 修复：使用原始ID供后续copy使用
        original_user_id = data['chat_id']
        original_msg_id = data['message_id']
        
        approve_btn = f"approve:{original_user_id}:{original_msg_id}"
        decline_btn = f"decline:{original_user_id}:{original_msg_id}"
        
        markup = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ 通过", callback_data=approve_btn),
            InlineKeyboardButton("❌ 拒绝", callback_data=decline_btn),
        ]])
        
        await context.bot.edit_message_reply_markup(
            chat_id=ADMIN_GROUP_ID,
            message_id=sent_msg.message_id,
            reply_markup=markup
        )
        
        # 发送成功提示，并带上返回菜单按钮，解决"无法返回"的问题
        success_kb = [[InlineKeyboardButton("⬅️ 返回主菜单", callback_data='back_to_main')]]
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="✅ <b>提交成功！</b>\n\n您的作品已提交审核，请耐心等待。",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(success_kb)
        )
        
        # 尝试删除用户发的原始媒体消息 (如果配置允许，实现真正的垃圾场清理)
        # 注意：这可能会导致机器人无法在 handle_approval 里再次 copy 消息到频道(如果时间过久)。
        # 建议：仅删除机器人的交互消息，保留用户的原始媒体作为存档，或者告知用户。
        # 这里我们已经清理了所有过程中的文字交互，界面已经很干净了。
        
    except Exception as e:
        logger.error(f"提交审核失败: {e}")
        err_kb = [[InlineKeyboardButton("⬅️ 返回主菜单", callback_data='back_to_main')]]
        await context.bot.send_message(chat_id=query.message.chat_id, text=f"❌ 提交失败: {e}", reply_markup=InlineKeyboardMarkup(err_kb))

    context.user_data.pop('submission_data', None)
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("操作已取消。", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ 返回主菜单", callback_data='back_to_main')]]))
    context.user_data.pop('submission_data', None)
    return ConversationHandler.END


# ================== 我的作品列表 (保持不变) ==================

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
    
    # 记录提示消息ID
    msg = await query.edit_message_text(f"🗑️ <b>删除模式</b>\n\n请回复您要删除的作品序号。\n该作品将从机器人记录和频道中<b>永久删除</b>。\n\n回复 /cancel 取消。", parse_mode=ParseMode.HTML)
    context.user_data['last_bot_msg'] = msg.message_id
    
    return DELETING_WORK

async def handle_delete_work_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.message.from_user.id
    text = update.message.text.strip()
    chat_id = update.message.chat_id
    
    # 清理对话
    await safe_delete_message(context.bot, chat_id, update.message.message_id)
    await safe_delete_message(context.bot, chat_id, context.user_data.get('last_bot_msg'))
    
    if not text.isdigit():
        msg = await update.message.reply_text("❌ 请输入数字序号。", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ 返回主菜单", callback_data='back_to_main')]]))
        context.user_data['last_bot_msg'] = msg.message_id
        return DELETING_WORK
    offset = int(text) - 1
    if offset < 0:
         msg = await update.message.reply_text("❌ 序号无效。", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ 返回主菜单", callback_data='back_to_main')]]))
         context.user_data['last_bot_msg'] = msg.message_id
         return DELETING_WORK

    pool = await get_pool()
    async with pool.acquire() as conn:
        target_post = await conn.fetchrow("SELECT id, channel_message_id, content_text FROM submissions WHERE user_id = $1 ORDER BY timestamp DESC LIMIT 1 OFFSET $2", user_id, offset)
        if not target_post:
            msg = await update.message.reply_text("❌ 找不到该序号对应的作品。", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ 返回主菜单", callback_data='back_to_main')]]))
            context.user_data['last_bot_msg'] = msg.message_id
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
            await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ 返回主菜单", callback_data='back_to_main')]]))
            
        except Exception as e:
            logger.error(f"删除过程出错: {e}")
            await update.message.reply_text("❌ 删除时发生系统错误。", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ 返回主菜单", callback_data='back_to_main')]]))

    context.user_data.pop('delete_work_page', None)
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
