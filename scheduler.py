"""
Scheduler module for the Video automation pipeline.
Configures and launches AsyncIOScheduler to automatically scrape trending news feeds on a daily basis.
Enables real-time cron rescheduling dynamically triggered from Telegram bot chat updates.
"""

import logging
import requests
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import config
import supabase_client
import scraper
import telegram_bot

logger = logging.getLogger("scheduler")

# Create AsyncIOScheduler
scheduler = AsyncIOScheduler()

async def daily_job():
    """
    Automatic daily news scraper trigger.
    1. Scrapes feeds and parses body articles.
    2. Constructs interactive inline menus of results.
    3. Pushes selections to user chats for simple tapping triggers.
    """
    print("⏰ Scheduler: Triggering automatic daily tech news scraping process...")
    try:
        # Trigger full RSS scraping process
        articles = scraper.fetch_trending_topics()
        
        if not articles:
            print("⏰ Scheduler: Daily news scrape returned no new entries.")
            return
            
        print("⏰ Scheduler: Scraping completed. Formulating Telegram topics notification...")
        
        # Query recently scraped jobs
        res = supabase_client.supabase.table("jobs").select("*").eq("status", "scraped").order("created_at", desc=True).limit(5).execute()
        jobs = res.data
        
        if jobs:
            # Recreate inline keyboards
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
            keyboard = []
            for job in jobs:
                keyboard.append([InlineKeyboardButton(f"🔥 {job['topic'][:40]}...", callback_data=f"topic_select_{job['id']}")])
                
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            # Dispatch directly via Bot REST API for simplicity
            url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
            payload = {
                "chat_id": config.TELEGRAM_CHAT_ID,
                "text": "🔥 *Daily Automated Trending Topics Are Ready! Pick one:*",
                "parse_mode": "Markdown",
                "reply_markup": reply_markup.to_dict()
            }
            
            resp = requests.post(url, json=payload, timeout=15)
            if resp.status_code == 200:
                print("⏰ Scheduler: Successfully pushed automatic topics card to Telegram.")
            else:
                print(f"❌ Scheduler: Failed to push topics card to Telegram: {resp.text}")
                
    except Exception as e:
        err_msg = f"Failed to run automated scheduler daily job: {e}"
        print(f"❌ {err_msg}")
        supabase_client.send_telegram_alert(err_msg)

async def start_scheduler():
    """
    Starts the AsyncIOScheduler. Loads configured schedules from Supabase on launch.
    """
    print("⏰ Scheduler: Initializing APScheduler engine...")
    try:
        settings = supabase_client.get_settings()
        cron_time = settings.get("cron_time", "13:00")
        
        # Parse hours and minutes
        hour, minute = cron_time.split(":")
        
        # Add cron trigger job
        scheduler.add_job(
            daily_job,
            trigger='cron',
            hour=int(hour),
            minute=int(minute),
            id='daily_scrape'
        )
        
        # Set reference in telegram_bot to allow dynamic reschedule commands
        telegram_bot.scheduler_ref = scheduler
        
        scheduler.start()
        print(f"✅ Scheduler: APScheduler engine running. Next scrape scheduled at {cron_time} daily.")
        
    except Exception as e:
        err_msg = f"APScheduler failed to launch: {e}"
        print(f"❌ {err_msg}")
        supabase_client.send_telegram_alert(err_msg)

if __name__ == "__main__":
    # Test script entry point
    import asyncio
    print("⏰ Scheduler: Local scheduler diagnostic starting...")
    async def run_test():
        await start_scheduler()
    asyncio.run(run_test())
