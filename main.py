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
    WAITING_CAPTION,
    CONFIRM_SUBMISSION,
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
    handle_media_input,
    handle_add_caption_choice,
    handle_caption_text,
    handle_confirm_submission,
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
    机器人主程序 (V10.6 - UX极致优化版)
    """
    USE_PROXY = False 
    PROXY_URL = "http://127.0.0.1:7890"
    
    builder = Application.builder().token(TOKEN)
    
    if USE_PROXY:
        builder = builder.request(HTTPXRequest(proxy=PROXY_URL))
    
    application = builder.post_init(setup_database).build()

    # 主对话处理器
    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            # 【修复】这里添加 back_to_main，确保流程结束(END)后点击按钮依然能触发主菜单
            CallbackQueryHandler(back_to_main, pattern='^back_to_main$')
        ],
        states={
            CHOOSING: [
                CallbackQueryHandler(prompt_submission, pattern='^submit_post$'),
                CallbackQueryHandler(navigate_my_posts, pattern='^my_posts_page:'),
                CallbackQueryHandler(show_my_collections, pattern='^my_collections_page:'),
            ],
            
            # 发布流程
            GETTING_POST: [
                MessageHandler(filters.ChatType.PRIVATE & ~filters.COMMAND, handle_media_input),
                # 允许在发图阶段直接点击返回
                CallbackQueryHandler(back_to_main, pattern='^back_to_main$') 
            ],
            WAITING_CAPTION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_caption_text),
                CallbackQueryHandler(handle_add_caption_choice, pattern='^(add_caption_yes|add_caption_no)$'),
                CallbackQueryHandler(back_to_main, pattern='^back_to_main$')
            ],
            CONFIRM_SUBMISSION: [
                CallbackQueryHandler(handle_confirm_submission, pattern='^(confirm_send|confirm_cancel)$'),
                CallbackQueryHandler(back_to_main, pattern='^back_to_main$')
            ],

            # 浏览/管理流程
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
                MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, handle_delete_work_input),
                # 允许在删除输入阶段点击返回
                CallbackQueryHandler(back_to_main, pattern='^back_to_main$')
            ]
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CommandHandler("start", start),
            # 【兜底】防止任何状态下卡住
            CallbackQueryHandler(back_to_main, pattern='^back_to_main$')
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
    
    logger.info("🚀 机器人 V10.6 启动成功！(界面洁癖优化+全流程返回)")
    
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
