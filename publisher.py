"""
Publisher module for the Video automation pipeline.
Manages automatic publishing of successfully approved mp4 videos to YouTube Shorts.
Leverages the official Google APIs Client Library and offline OAuth2 refresh tokens.
"""

import os
import requests
import google.oauth2.credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
import config
import supabase_client

def publish_to_youtube(video_path: str, title: str, description: str) -> str:
    """
    Publishes a video to YouTube Shorts using Google YouTube Data API v3.
    If the video_path is a URL, downloads it to a temp local file first.
    Returns the YouTube video ID on success, or None on failure.
    """
    print(f"🚀 Publisher: Initiating YouTube Shorts upload process...")
    temp_downloaded = False
    
    # 1. Download file locally if a remote URL was provided
    if video_path.startswith("http://") or video_path.startswith("https://"):
        print(f"📥 Publisher: Remote URL detected. Downloading video to local temp file: {video_path}")
        try:
            local_temp = os.path.join(config.OUTPUT_DIR, "temp_upload.mp4")
            resp = requests.get(video_path, timeout=120)
            resp.raise_for_status()
            with open(local_temp, "wb") as f:
                f.write(resp.content)
            video_path = local_temp
            temp_downloaded = True
            print("✅ Publisher: Video downloaded successfully.")
        except Exception as dl_err:
            err_msg = f"Failed to download remote video for YouTube upload: {dl_err}"
            print(f"❌ {err_msg}")
            supabase_client.send_telegram_alert(err_msg)
            return None
            
    # 2. Check local path exists
    if not os.path.exists(video_path):
        err_msg = f"Video file not found at path: {video_path}"
        print(f"❌ {err_msg}")
        supabase_client.send_telegram_alert(err_msg)
        return None
        
    try:
        # Load credentials
        print("🔑 Publisher: Authenticating with YouTube via OAuth2 refresh token...")
        credentials = google.oauth2.credentials.Credentials(
            token=None,
            refresh_token=config.YOUTUBE_REFRESH_TOKEN,
            client_id=config.YOUTUBE_CLIENT_ID,
            client_secret=config.YOUTUBE_CLIENT_SECRET,
            token_uri='https://oauth2.googleapis.com/token'
        )
        
        # Build API client service
        youtube = build('youtube', 'v3', credentials=credentials)
        
        # Parse tags from description hashtags
        tags = [t.strip("#").strip(",") for t in description.split() if t.startswith("#")]
        
        # Build upload body metadata
        body = {
            "snippet": {
                "title": title[:100],  # YouTube titles are limited to 100 characters
                "description": f"{description}\n\n#Shorts",
                "categoryId": "28",  # Science & Technology category
                "tags": tags
            },
            "status": {
                "privacyStatus": "public",
                "selfDeclaredMadeForKids": False
            }
        }
        
        media = MediaFileUpload(video_path, chunksize=-1, resumable=True)
        
        print(f"📤 Publisher: Uploading video file to YouTube...")
        request = youtube.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media
        )
        
        response = request.execute()
        video_id = response.get("id")
        
        if video_id:
            msg = f"✅ Live on YouTube Shorts!\n🔗 https://youtube.com/shorts/{video_id}"
            print(f"🎉 Publisher: {msg}")
            
            # Send immediate success message to Telegram
            supabase_client.send_telegram_alert(msg)
            return video_id
        else:
            raise ValueError("YouTube API did not return a valid video ID.")
            
    except Exception as e:
        err_msg = f"YouTube publishing failure: {e}"
        print(f"❌ {err_msg}")
        supabase_client.send_telegram_alert(err_msg)
        return None
        
    finally:
        # Cleanup temp file
        if temp_downloaded and os.path.exists(video_path):
            try:
                os.remove(video_path)
                print("🧹 Publisher: Cleaned up temporary download file.")
            except:
                pass

if __name__ == "__main__":
    # Test stub
    print("⚠️ Publisher: YouTube Publisher is active. Run from main pipeline.")
