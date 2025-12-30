# main.py

import logging
import asyncio
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ConversationHandler,
    filters,
    ContextTypes,
)
from telegram.request import HTTPXRequest
from telegram import Update

from config import (
    TOKEN, 
    CHOOSING, 
    GETTING_POST,
    WAITING_CAPTION,      # 新增
    CONFIRM_SUBMISSION,   # 新增
    BROWSING_POSTS, 
    BROWSING_COLLECTIONS,
    COMMENTING,
    DELETING_COMMENT,
    DELETING_WORK
)
from database import setup_database, close_pool
from handlers.start_menu import start, back_to_main
from handlers.submission import (
    prompt_submission, 
    handle_media_input,       # 原 handle_new_post 改名，处理初始输入
    handle_add_caption_choice, # 处理是否加文案的选择
    handle_caption_text,       # 处理补发的文案文本
    handle_confirm_submission, # 处理最终确认
    navigate_my_posts, 
    show_my_collections, 
    prompt_delete_work,
    handle_delete_work_input,
    cancel
)
from handlers.approval import handle_approval, handle_rejection
from handlers.channel_interact import handle_channel_interaction
from handlers.commenting import prompt_comment, handle_new_comment
from handlers.comment_management import show_delete_comment_menu, handle_delete_comment_input


logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', 
    level=logging.INFO
)
logger = logging.getLogger(__name__)


def main():
    """
    机器人主程序 (V10.5 - 投稿流程优化版)
    """
    USE_PROXY = False 
    PROXY_URL = "http://127.0.0.1:7890"
    
    builder = Application.builder().token(TOKEN)
    
    if USE_PROXY:
        builder = builder.request(HTTPXRequest(proxy=PROXY_URL))
    
    application = builder.post_init(setup_database).build()

    # 主对话处理器
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            CHOOSING: [
                CallbackQueryHandler(prompt_submission, pattern='^submit_post$'),
                CallbackQueryHandler(navigate_my_posts, pattern='^my_posts_page:'),
                CallbackQueryHandler(show_my_collections, pattern='^my_collections_page:'),
            ],
            
            # 阶段1：接收初始投稿（图片/视频/文字）
            GETTING_POST: [
                MessageHandler(filters.ChatType.PRIVATE & ~filters.COMMAND, handle_media_input),
            ],
            
            # 阶段2：用户决定是否补发文案
            WAITING_CAPTION: [
                # 用户点击了“我要加文案” -> 等待文本
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_caption_text),
                # 用户点击了“直接发送”或“添加文案”的按钮
                CallbackQueryHandler(handle_add_caption_choice, pattern='^(add_caption_yes|add_caption_no)$')
            ],
            
            # 阶段3：最终确认
            CONFIRM_SUBMISSION: [
                CallbackQueryHandler(handle_confirm_submission, pattern='^(confirm_send|confirm_cancel)$')
            ],

            # --- 以下保持不变 ---
            BROWSING_POSTS: [
                CallbackQueryHandler(navigate_my_posts, pattern='^my_posts_page:'),
                CallbackQueryHandler(prompt_delete_work, pattern='^delete_work_prompt:'),
                CallbackQueryHandler(back_to_main, pattern='^back_to_main$'),
            ],
            BROWSING_COLLECTIONS: [
                CallbackQueryHandler(show_my_collections, pattern='^my_collections_page:'),
                CallbackQueryHandler(back_to_main, pattern='^back_to_main$'),
            ],
            COMMENTING: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_new_comment)
            ],
            DELETING_COMMENT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, handle_delete_comment_input)
            ],
            DELETING_WORK: [
                MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, handle_delete_work_input)
            ]
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CommandHandler("start", start)
        ],
        allow_reentry=True,
        per_chat=True,
        per_user=True,
        name="main_conversation",
    )
    
    application.add_handler(conv_handler)
    application.add_handler(CallbackQueryHandler(handle_approval, pattern='^approve:'))
    application.add_handler(CallbackQueryHandler(handle_rejection, pattern='^decline:'))
    application.add_handler(CallbackQueryHandler(handle_channel_interaction, pattern='^(react|collect|comment)'))
    
    async def debug_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.message and update.message.text:
            logger.warning(f"⚠️ 未处理的消息: '{update.message.text}'")
    
    application.add_handler(MessageHandler(filters.TEXT & filters.ChatType.PRIVATE, debug_handler), group=999)
    
    logger.info("🚀 机器人 V10.5 启动成功！(优化投稿流程)")
    
    try:
        application.run_polling(drop_pending_updates=True)
    except Exception as e:
        logger.error(f"❌ 机器人运行错误: {e}")
    finally:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(close_pool())
        else:
            loop.run_until_complete(close_pool())


if __name__ == '__main__':
    main()
