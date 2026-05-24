"""
Telegram Bot module for the Video automation pipeline.
Provides a premium user interface to pick daily topics, test/set narration voices, update schedule timings,
provide feedback for dynamic script rewrites, and approve and publish rendered videos.
"""

import os
import logging
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes
)
import config
import supabase_client
import gemini_engine
import asset_builder
import video_renderer
import publisher

logger = logging.getLogger("telegram_bot")

# Global scheduler reference to allow dynamic rescheduling from within bot handlers
scheduler_ref = None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Welcomes the user and explains all commands and features.
    """
    try:
        welcome_text = (
            "🔥 *Welcome to short-form Video Automation Pipeline!* 🔥\n\n"
            "This bot fully automates your YouTube Shorts creation and publishing workflow.\n\n"
            "*Available Commands:*\n"
            "📱 `/start` - Show this instructions menu.\n"
            "⏰ `/scheduler HH:MM` - Set daily news scraping & script creation schedule.\n"
            "🔊 `/voice` - Test and select the default AI TTS voice profile.\n"
            "📰 `/topics` - Trigger a news scrape and select today's viral article."
        )
        await update.message.reply_text(welcome_text, parse_mode="Markdown")
        print("🤖 Bot: Start welcome message sent successfully.")
    except Exception as e:
        print(f"❌ Bot Error in start handler: {e}")

async def set_scheduler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Updates the daily automatic scrape and script time in Supabase.
    """
    try:
        if not context.args or len(context.args) < 1:
            await update.message.reply_text("⚠️ Usage: `/scheduler HH:MM` (e.g. `/scheduler 14:30`)", parse_mode="Markdown")
            return
            
        time_str = context.args[0]
        # Validate format
        parts = time_str.split(":")
        if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
            await update.message.reply_text("⚠️ Invalid format. Please specify `HH:MM` (24-hour style).")
            return
            
        hour, minute = int(parts[0]), int(parts[1])
        if hour < 0 or hour > 23 or minute < 0 or minute > 59:
            await update.message.reply_text("⚠️ Invalid time constraints. Hours must be 0-23 and minutes 0-59.")
            return
            
        # Update settings table
        supabase_client.update_settings(cron_time=time_str)
        
        # Dynamically update APScheduler job if active
        global scheduler_ref
        if scheduler_ref:
            try:
                scheduler_ref.reschedule_job(
                    'daily_scrape',
                    trigger='cron',
                    hour=hour,
                    minute=minute
                )
                print(f"⏰ Scheduler: APScheduler updated to trigger daily at {time_str}.")
            except Exception as sch_err:
                print(f"⚠️ Scheduler: APScheduler rescheduling exception: {sch_err}")
                
        await update.message.reply_text(f"✅ *Scheduler updated to {time_str} daily!*", parse_mode="Markdown")
        print(f"🤖 Bot: Scheduler time successfully set to {time_str}.")
        
    except Exception as e:
        err_msg = f"Bot scheduler update failed: {e}"
        print(f"❌ {err_msg}")
        supabase_client.send_telegram_alert(err_msg)

async def select_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Renders an inline keyboard menu showing available AI TTS voice profiles.
    """
    try:
        keyboard = [
            [
                InlineKeyboardButton("🔊 Test Andrew", callback_data="voice_test_en-US-AndrewNeural"),
                InlineKeyboardButton("✅ Set Andrew", callback_data="voice_set_en-US-AndrewNeural")
            ],
            [
                InlineKeyboardButton("🔊 Test Jenny", callback_data="voice_test_en-US-JennyNeural"),
                InlineKeyboardButton("✅ Set Jenny", callback_data="voice_set_en-US-JennyNeural")
            ],
            [
                InlineKeyboardButton("🔊 Test Madhur", callback_data="voice_test_hi-IN-MadhurNeural"),
                InlineKeyboardButton("✅ Set Madhur", callback_data="voice_set_hi-IN-MadhurNeural")
            ],
            [
                InlineKeyboardButton("🔊 Test Aarohi", callback_data="voice_test_mr-IN-AarohiNeural"),
                InlineKeyboardButton("✅ Set Aarohi", callback_data="voice_set_mr-IN-AarohiNeural")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "🗣️ *Select Default Narration Voice:*\n"
            "Andrew (English Male)\nJenny (English Female)\n"
            "Madhur (Hindi Male)\nAarohi (Marathi Female)\n\n"
            "Test sample audio or set default:",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
    except Exception as e:
        print(f"❌ Bot Error in select_voice command: {e}")

async def show_scraped_topics(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Manually triggers RSS scrapes, shows top 5 trending topics using inline buttons.
    """
    try:
        await update.message.reply_text("🔍 Scraping RSS feeds for today's hottest tech trends...")
        
        # Trigger RSS scraping
        import scraper
        scraper.fetch_trending_topics()
        
        # Query recently scraped jobs with status 'scraped'
        res = supabase_client.supabase.table("jobs").select("*").eq("status", "scraped").order("created_at", desc=True).limit(5).execute()
        jobs = res.data
        
        if not jobs:
            await update.message.reply_text("⚠️ No trending articles scraped. Ensure RSS feeds are responsive.")
            return
            
        keyboard = []
        for job in jobs:
            keyboard.append([InlineKeyboardButton(f"🔥 {job['topic'][:40]}...", callback_data=f"topic_select_{job['id']}")])
            
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("🔥 *Today's Trending Topics — Pick One:*", reply_markup=reply_markup, parse_mode="Markdown")
        
    except Exception as e:
        err_msg = f"Failed to retrieve or display news topics: {e}"
        print(f"❌ {err_msg}")
        supabase_client.send_telegram_alert(err_msg)

async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Dispatches callback events originating from voice tests, voice settings, topic selections, and approvals.
    """
    query = update.callback_query
    await query.answer()
    
    data = query.data
    print(f"🤖 Bot: Callback query received: {data}")
    
    try:
        # Voice Sample Audios Test trigger
        if data.startswith("voice_test_"):
            voice_id = data.replace("voice_test_", "")
            sample_path = os.path.join(config.ASSETS_DIR, "voice_sample.mp3")
            
            # Clean old samples
            if os.path.exists(sample_path):
                os.remove(sample_path)
                
            await query.edit_message_text(f"⏳ Generating 5-second vocal sample for `{voice_id}`...")
            
            import edge_tts
            communicate = edge_tts.Communicate(
                text="This is a quick preview of your automated narrator voice output.",
                voice=voice_id
            )
            await communicate.save(sample_path)
            
            # Send sample as Telegram voice note
            with open(sample_path, "rb") as f:
                await query.message.reply_voice(voice=f, caption=f"🗣️ Sample preview: {voice_id}")
            
            # Re-render menu
            await query.message.reply_text("✅ Voice note sent! Tap Set Default if you like it.")
            
        # Set default voice configuration
        elif data.startswith("voice_set_"):
            voice_id = data.replace("voice_set_", "")
            supabase_client.update_settings(voice_id=voice_id)
            await query.edit_message_text(f"✅ Default voice successfully set to `{voice_id}`!")
            
        # Topic selection event
        elif data.startswith("topic_select_"):
            job_id = data.replace("topic_select_", "")
            await query.edit_message_text("✅ Got it! Generating script. This takes about a minute...")
            
            # Execute pipeline task in background thread/task to prevent event loop blockages
            asyncio.create_task(execute_generation_pipeline(job_id, query.message))
            
        # Approval trigger
        elif data.startswith("approve_"):
            job_id = data.replace("approve_", "")
            await query.edit_message_text("🚀 Uploading to YouTube Shorts... Please wait.")
            
            # Execute publishing in background task
            asyncio.create_task(execute_publishing_pipeline(job_id, query.message))
            
        # Rejection trigger
        elif data.startswith("reject_"):
            job_id = data.replace("reject_", "")
            context.user_data["awaiting_feedback_for_job"] = job_id
            await query.edit_message_text("💬 *Tell me exactly what to fix:*\nType your modifications directly below.", parse_mode="Markdown")
            
    except Exception as e:
        err_msg = f"Bot Callback dispatch failure: {e}"
        print(f"❌ {err_msg}")
        supabase_client.send_telegram_alert(err_msg)

async def handle_user_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Intercepts text messages. Used for catching edit feedbacks.
    """
    try:
        # Check if user was prompted for edit reviews feedback
        job_id = context.user_data.get("awaiting_feedback_for_job")
        if job_id:
            feedback_text = update.message.text
            # Flush state
            context.user_data.pop("awaiting_feedback_for_job", None)
            
            await update.message.reply_text("🔄 Rewriting script incorporating your feedback...")
            
            # Run rewrite pipeline asynchronously
            asyncio.create_task(execute_generation_pipeline(job_id, update.message, feedback=feedback_text))
        else:
            # Simple text echo fallback
            await update.message.reply_text("Use commands starting with `/` or tap buttons to interface.")
    except Exception as e:
        print(f"❌ Bot Error in text message processor: {e}")

async def execute_generation_pipeline(job_id: str, bot_message, feedback: str = None):
    """
    Coordinates AI Scripting, Asset construction, and dispatches GitHub Actions remote renderer.
    """
    print(f"🤖 Bot Pipeline: Coordinating generation script for Job {job_id}...")
    try:
        job = supabase_client.get_job(job_id)
        if not job:
            await bot_message.reply_text("❌ Error: Selected article job not found.")
            return
            
        # Phase 3: Gemini Engine Generation
        old_script_str = ""
        if feedback:
            old_script_str = str(job.get("gemini_json"))
            supabase_client.update_job(job_id=job_id, status="pending", feedback=feedback)
        else:
            supabase_client.update_job(job_id=job_id, status="pending")
            
        print("🤖 Bot Pipeline: Triggering gemini scripting...")
        script_data = gemini_engine.generate_script(
            full_article_text=job["full_article_text"],
            feedback=feedback,
            old_script=old_script_str,
            job_id=job_id
        )
        
        if not script_data:
            await bot_message.reply_text("❌ Error: Script generation failed. API failure alerted.")
            return
            
        # Notify user of scripting success
        await bot_message.reply_text(f"📝 *Script Written!* Brand: {script_data['brand_keyword']}\n🤖 Triggering remote GitHub Action workflow to compile media...")
        
        # Set job status to rendering
        supabase_client.update_job(job_id=job_id, status="rendering")
        
        # Phase 5: Trigger GitHub actions remotely
        success = video_renderer.trigger_github_render(job_id)
        if not success:
            await bot_message.reply_text("❌ Error: Failed to trigger GitHub Actions remote compiler.")
            return
            
        # Poll for completion status (Async loop)
        await bot_message.reply_text("⏳ Compiling assets and rendering video on GitHub Actions runner... (Takes ~2-3 mins)")
        
        loop = asyncio.get_event_loop()
        # Polling runs in executors thread to prevent main loop blocks
        render_success = await loop.run_in_executor(None, video_renderer.check_github_action_status, job_id)
        
        if not render_success:
            await bot_message.reply_text("❌ Error: Video compilation failed or timed out on remote runner.")
            return
            
        # Query updated video details
        updated_job = supabase_client.get_job(job_id)
        video_url = updated_job.get("video_url")
        
        if not video_url:
            await bot_message.reply_text("❌ Error: Render was marked success, but no public video URL found.")
            return
            
        # Send video file with approval keys
        print(f"🤖 Bot Pipeline: Dispatching final video for reviews from {video_url}...")
        keyboard = [
            [
                InlineKeyboardButton("✅ Approve & Publish", callback_data=f"approve_{job_id}"),
                InlineKeyboardButton("❌ Reject & Edit", callback_data=f"reject_{job_id}")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        caption = f"🎬 *{script_data['title']}*\n\n{script_data['description']}"
        
        # Download and send the actual video file for seamless previewing
        await bot_message.reply_text("📥 Downloading rendering for preview...")
        local_path = os.path.join(config.OUTPUT_DIR, f"preview_{job_id}.mp4")
        
        # Download
        resp = requests.get(video_url, timeout=60)
        with open(local_path, "wb") as f:
            f.write(resp.content)
            
        with open(local_path, "rb") as video_file:
            sent_msg = await bot_message.reply_video(
                video=video_file,
                caption=caption,
                reply_markup=reply_markup,
                parse_mode="Markdown"
            )
            
        # Update job with Telegram message ID for reference
        supabase_client.update_job(job_id=job_id, telegram_message_id=sent_msg.message_id)
        print("🎉 Bot Pipeline: Video sent for approval.")
        
    except Exception as e:
        err_msg = f"Bot Pipeline execution failed: {e}"
        print(f"❌ {err_msg}")
        supabase_client.send_telegram_alert(err_msg)
        await bot_message.reply_text("❌ Critical Pipeline Error occurred. Administrator has been alerted.")

async def execute_publishing_pipeline(job_id: str, bot_message):
    """
    Coordinates YouTube Short publication for the approved video.
    """
    print(f"🤖 Bot Pipeline: Commencing publication for Job {job_id}...")
    try:
        job = supabase_client.get_job(job_id)
        if not job or not job.get("video_url"):
            await bot_message.reply_text("❌ Error: Job or video files not found.")
            return
            
        gemini_json = job.get("gemini_json", {})
        title = gemini_json.get("title", "Awesome Tech Shorts")
        description = gemini_json.get("description", "A viral automated tech short.")
        
        # Phase 6: Publish to YouTube Shorts
        loop = asyncio.get_event_loop()
        video_id = await loop.run_in_executor(
            None,
            publisher.publish_to_youtube,
            job["video_url"],
            title,
            description
        )
        
        if video_id:
            # Mark job status as done in db
            supabase_client.update_job(job_id=job_id, status="done")
            await bot_message.reply_text(f"🚀 *Short successfully published!*\n🔗 https://youtube.com/shorts/{video_id}", parse_mode="Markdown")
        else:
            await bot_message.reply_text("❌ YouTube upload failed. Error details have been notified.")
            
    except Exception as e:
        err_msg = f"Bot pipeline publishing failed: {e}"
        print(f"❌ {err_msg}")
        supabase_client.send_telegram_alert(err_msg)
        await bot_message.reply_text("❌ Critical upload failure.")

def test_bot():
    """
    Standalone diagnostic script triggered by developer commands.
    """
    try:
        print("🔌 Bot: testing telegram connection...")
        import requests
        url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/getMe"
        resp = requests.get(url, timeout=10).json()
        print(f"🔌 Bot response: {resp}")
        return resp.get("ok", False)
    except Exception as e:
        print(f"❌ Bot connection test failed: {e}")
        return False

if __name__ == "__main__":
    test_bot()
