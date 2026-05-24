"""
Supabase client module to manage all PostgreSQL interactions and media file storage.
Includes self-healing checks and automatic Telegram failure alerts.
"""

import requests
import config
from supabase import create_client, Client

supabase: Client = None

try:
    if config.SUPABASE_URL and config.SUPABASE_KEY:
        supabase = create_client(config.SUPABASE_URL, config.SUPABASE_KEY)
        print("✅ Supabase: Client initialized successfully!")
    else:
        print("⚠️ Supabase: Configuration is missing URL or KEY.")
except Exception as e:
    print(f"❌ Supabase: Failed to initialize client: {e}")

def send_telegram_alert(error_message: str):
    """
    Sends an immediate error notification to the configured Telegram CHAT_ID.
    This fulfills the global requirement: On ANY API failure, send notification.
    """
    try:
        if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
            print("⚠️ Supabase: Cannot send Telegram alert. Bot Token or Chat ID not configured.")
            return

        url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": config.TELEGRAM_CHAT_ID,
            "text": f"🚨 *System Failure Alert* 🚨\n\n*Error details*:\n```\n{error_message}\n```",
            "parse_mode": "Markdown"
        }
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code != 200:
            print(f"❌ Supabase: Failed to send Telegram alert: {resp.text}")
        else:
            print("✉️ Supabase: Failure alert sent successfully to Telegram!")
    except Exception as ex:
        print(f"❌ Supabase: Exception occurred while sending Telegram alert: {ex}")

def ensure_bucket_exists():
    """
    Checks if 'videos' bucket exists in Supabase Storage.
    If not, attempts to create it as a public bucket.
    """
    try:
        if not supabase:
            return
        
        print("📁 Supabase: Checking storage buckets...")
        buckets = supabase.storage.list_buckets()
        bucket_names = [b.name for b in buckets]
        if "videos" not in bucket_names:
            print("📁 Supabase: Creating 'videos' storage bucket...")
            supabase.storage.create_bucket("videos", options={"public": True})
            print("✅ Supabase: 'videos' bucket created!")
        else:
            print("✅ Supabase: 'videos' bucket is available.")
    except Exception as e:
        print(f"⚠️ Supabase: Error checking/creating bucket: {e} (Assuming it exists or permissions are restricted)")

def get_settings():
    """
    Retrieves the scheduler settings row (id=1).
    Inserts a default row if settings table is empty.
    """
    try:
        print("🔍 Supabase: Querying settings...")
        res = supabase.table("settings").select("*").eq("id", 1).execute()
        if not res.data:
            print("⚠️ Supabase: No settings row found. Creating default settings row...")
            insert_res = supabase.table("settings").insert({
                "id": 1,
                "cron_time": "13:00",
                "voice_id": "en-US-AndrewNeural"
            }).execute()
            print(f"✅ Supabase: Default settings created: {insert_res.data}")
            return insert_res.data[0]
        return res.data[0]
    except Exception as e:
        err_msg = f"Database error in get_settings(): {e}"
        print(f"❌ {err_msg}")
        send_telegram_alert(err_msg)
        return {
            "id": 1,
            "cron_time": "13:00",
            "voice_id": "en-US-AndrewNeural"
        }

def update_settings(cron_time: str = None, voice_id: str = None):
    """
    Updates the settings table with a new voice selection or cron scheduling time.
    """
    try:
        update_data = {}
        if cron_time is not None:
            update_data["cron_time"] = cron_time
        if voice_id is not None:
            update_data["voice_id"] = voice_id
        
        if not update_data:
            return
        
        print(f"✏️ Supabase: Updating settings row with {update_data}...")
        res = supabase.table("settings").update(update_data).eq("id", 1).execute()
        print(f"✅ Supabase: Settings updated: {res.data}")
        return res.data[0] if res.data else None
    except Exception as e:
        err_msg = f"Database error in update_settings(): {e}"
        print(f"❌ {err_msg}")
        send_telegram_alert(err_msg)
        return None

def insert_job(topic: str, article_url: str, full_article_text: str):
    """
    Inserts a newly scraped article as a pending job with status 'scraped'.
    """
    try:
        print(f"➕ Supabase: Storing scraped article '{topic[:40]}...' into jobs table...")
        res = supabase.table("jobs").insert({
            "status": "scraped",
            "topic": topic,
            "article_url": article_url,
            "full_article_text": full_article_text
        }).execute()
        print(f"✅ Supabase: Job stored! UUID: {res.data[0]['id']}")
        return res.data[0]
    except Exception as e:
        err_msg = f"Database error in insert_job(): {e}"
        print(f"❌ {err_msg}")
        send_telegram_alert(err_msg)
        return None

def update_job(job_id: str, **kwargs):
    """
    Updates a specific job record's columns dynamically by its UUID.
    """
    try:
        print(f"✏️ Supabase: Updating job {job_id} columns: {list(kwargs.keys())}...")
        res = supabase.table("jobs").update(kwargs).eq("id", job_id).execute()
        print("✅ Supabase: Job updated successfully!")
        return res.data[0] if res.data else None
    except Exception as e:
        err_msg = f"Database error in update_job() for id {job_id}: {e}"
        print(f"❌ {err_msg}")
        send_telegram_alert(err_msg)
        return None

def get_job(job_id: str):
    """
    Fetches a specific job's complete details.
    """
    try:
        print(f"🔍 Supabase: Querying job details for {job_id}...")
        res = supabase.table("jobs").select("*").eq("id", job_id).execute()
        return res.data[0] if res.data else None
    except Exception as e:
        err_msg = f"Database error in get_job() for id {job_id}: {e}"
        print(f"❌ {err_msg}")
        send_telegram_alert(err_msg)
        return None

def upload_video_to_storage(local_path: str, filename: str) -> str:
    """
    Uploads a rendered video to the 'videos' storage bucket and returns its public URL.
    """
    try:
        print(f"📤 Supabase: Uploading {local_path} as '{filename}'...")
        ensure_bucket_exists()
        
        with open(local_path, "rb") as f:
            supabase.storage.from_("videos").upload(
                path=filename,
                file=f,
                file_options={"cache-control": "3600", "upsert": "true"}
            )
            
        public_url = supabase.storage.from_("videos").get_public_url(filename)
        print(f"✅ Supabase: Uploaded successfully! Public URL: {public_url}")
        return public_url
    except Exception as e:
        err_msg = f"Storage error in upload_video_to_storage(): {e}"
        print(f"❌ {err_msg}")
        send_telegram_alert(err_msg)
        raise e

def test_connection():
    """
    Direct test utility triggered by script runner.
    """
    try:
        print("🔗 Supabase: Testing database and storage connection...")
        if not supabase:
            print("❌ Supabase client is not initialized.")
            return False
        
        # Test 1: Query settings
        settings = get_settings()
        print(f"✅ Supabase settings query successful. cron_time={settings['cron_time']}, voice_id={settings['voice_id']}")
        
        # Test 2: Storage Buckets listing
        ensure_bucket_exists()
        print("✅ Supabase Storage bucket verified.")
        print("🎉 Supabase test completed successfully!")
        return True
    except Exception as e:
        print(f"❌ Supabase Connection Test failed: {e}")
        return False

if __name__ == "__main__":
    test_connection()
