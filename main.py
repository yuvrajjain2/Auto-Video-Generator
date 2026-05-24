"""
Main Entry Point for the Video Automation Pipeline.
Launches the asynchronous scheduler and Telegram Bot polling application simultaneously in a concurrent event loop.
"""

import asyncio
import logging
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters
import config
from scheduler import start_scheduler
from telegram_bot import (
    start, set_scheduler, select_voice, show_scraped_topics,
    handle_callback_query, handle_user_text
)

# Configure logging formats
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("main")

async def start_bot():
    """
    Initializes and starts the Telegram Bot polling process.
    Leverages python-telegram-bot v20+ async architecture.
    """
    print("🤖 Main: Initializing Telegram Bot app handlers...")
    
    if not config.TELEGRAM_BOT_TOKEN:
        print("❌ Main: Critical failure! TELEGRAM_BOT_TOKEN is missing in environment.")
        return
        
    try:
        # Build standard bot application
        application = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()
        
        # 1. Register Command Handlers
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("scheduler", set_scheduler))
        application.add_handler(CommandHandler("voice", select_voice))
        application.add_handler(CommandHandler("topics", show_scraped_topics))
        
        # 2. Register Callback Query Handlers
        application.add_handler(CallbackQueryHandler(handle_callback_query))
        
        # 3. Register Message Handlers (for script modification feedbacks)
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_user_text))
        
        print("🤖 Main: Starting Telegram Bot polling engine...")
        
        # Start bot polling asynchronously
        await application.initialize()
        await application.start()
        await application.updater.start_polling()
        
        print("✅ Main: Telegram Bot polling is active and listening for events!")
        
        # Keep bot active in the asyncio loop indefinitely
        while True:
            await asyncio.sleep(3600)
            
    except Exception as e:
        print(f"❌ Main: Failed to launch Telegram Bot: {e}")

async def main():
    """
    Main orchestrator for the video pipeline system.
    Bootstraps the settings configuration and runs scheduler and bot concurrently.
    """
    print("🚀 Main: Bootstrapping automated video production pipeline...")
    
    # Verify environment secrets
    config.validate_config()
    
    try:
        # Run the daily Scheduler and bot polling concurrently
        await asyncio.gather(
            start_scheduler(),
            start_bot()
        )
    except Exception as e:
        print(f"❌ Main: Critical failure in main pipeline event loop: {e}")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("\n👋 Main: System shutting down. Goodbye!")
