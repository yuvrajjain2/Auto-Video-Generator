"""
Video Renderer module for the Video automation pipeline.
Coordinates the GitHub Actions remote render dispatcher and implements the FFmpeg compilation engine running inside the GitHub Actions runner.
Applies Ken Burns zoom, transitions, audio ducking, brand logo overlay, whoosh sounds, and burned word subtitles.
"""

import os
import sys
import time
import asyncio
import argparse
import subprocess
import requests
import config
import supabase_client
import asset_builder

# Pull configuration values
GITHUB_PAT = config.GITHUB_PAT
GITHUB_REPO_OWNER = config.GITHUB_REPO_OWNER
GITHUB_REPO_NAME = config.GITHUB_REPO_NAME

def trigger_github_render(job_id: str) -> bool:
    """
    Triggers the GitHub Actions render workflow remotely via REST API.
    All secrets are passed dynamically from the local application to bypass GitHub secrets setup.
    """
    print(f"🚀 Video Renderer: Dispatching remote GitHub Action workflow for job {job_id}...")
    url = f"https://api.github.com/repos/{GITHUB_REPO_OWNER}/{GITHUB_REPO_NAME}/actions/workflows/render_video.yml/dispatches"
    
    headers = {
        "Authorization": f"Bearer {GITHUB_PAT}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28"
    }
    
    payload = {
        "ref": "main",
        "inputs": {
            "job_id": str(job_id),
            "supabase_url": config.SUPABASE_URL,
            "supabase_key": config.SUPABASE_KEY,
            "telegram_bot_token": config.TELEGRAM_BOT_TOKEN,
            "telegram_chat_id": config.TELEGRAM_CHAT_ID,
            "gemini_api_key": config.GEMINI_API_KEY,
            "youtube_client_id": config.YOUTUBE_CLIENT_ID,
            "youtube_client_secret": config.YOUTUBE_CLIENT_SECRET,
            "youtube_refresh_token": config.YOUTUBE_REFRESH_TOKEN,
            "github_pat": config.GITHUB_PAT,
            "github_repo_owner": config.GITHUB_REPO_OWNER,
            "github_repo_name": config.GITHUB_REPO_NAME,
            "pexels_api_key": config.PEXELS_API_KEY or ""
        }
    }
    
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=20)
        if response.status_code == 204:
            print("✅ Video Renderer: GitHub Action successfully triggered!")
            return True
        else:
            err_msg = f"GitHub trigger failed with status {response.status_code}: {response.text}"
            print(f"❌ {err_msg}")
            supabase_client.send_telegram_alert(err_msg)
            return False
    except Exception as e:
        err_msg = f"Failed to connect to GitHub API: {e}"
        print(f"❌ {err_msg}")
        supabase_client.send_telegram_alert(err_msg)
        return False

def get_latest_run_id() -> int:
    """
    Fetches the ID of the absolute latest workflow run in the repository.
    Used before dispatching to eliminate the race condition.
    """
    url = f"https://api.github.com/repos/{GITHUB_REPO_OWNER}/{GITHUB_REPO_NAME}/actions/runs"
    headers = {
        "Authorization": f"Bearer {GITHUB_PAT}",
        "Accept": "application/vnd.github+json"
    }
    try:
        response = requests.get(url, headers=headers, timeout=20)
        if response.status_code == 200:
            runs = response.json().get('workflow_runs', [])
            if runs:
                return runs[0].get('id')
    except Exception as e:
        print(f"⚠️ Video Renderer: Failed to fetch latest run ID: {e}")
    return None

def check_github_action_status(job_id: str, previous_latest_id: int = None) -> bool:
    """
    Polls the GitHub Action run status every 30 seconds for up to 20 minutes.
    Locks onto the newly dispatched run specifically, avoiding previous runs' confusion.
    """
    print("⏳ Video Renderer: Commencing status polling...")
    url = f"https://api.github.com/repos/{GITHUB_REPO_OWNER}/{GITHUB_REPO_NAME}/actions/runs"
    
    headers = {
        "Authorization": f"Bearer {GITHUB_PAT}",
        "Accept": "application/vnd.github+json"
    }
    
    # 1. If not provided, fetch latest run ID before our dispatch is processed
    if previous_latest_id is None:
        try:
            response = requests.get(url, headers=headers, timeout=20)
            if response.status_code == 200:
                runs = response.json().get('workflow_runs', [])
                if runs:
                    previous_latest_id = runs[0].get('id')
                    print(f"⏳ Video Renderer: Previous latest run ID was {previous_latest_id}")
        except Exception as e:
            print(f"⚠️ Video Renderer: Failed to fetch initial run: {e}")

    # Wait a small buffer for GitHub to register the dispatch
    print("⏳ Video Renderer: Waiting 8 seconds for GitHub to register the dispatch...")
    time.sleep(8)
    
    target_run_id = None
    
    # Poll every 30 seconds, max 40 attempts = 20 minutes
    for attempt in range(40):
        print(f"⏳ Video Renderer: Polling attempt {attempt + 1}/40...")
        try:
            response = requests.get(url, headers=headers, timeout=20)
            if response.status_code != 200:
                print(f"⚠️ Video Renderer: Status API returned code {response.status_code}")
                time.sleep(30)
                continue
                
            runs = response.json().get('workflow_runs', [])
            if runs:
                # If we haven't identified our target run yet, look for a new run
                if not target_run_id:
                    latest_run = runs[0]
                    latest_id = latest_run.get('id')
                    # If it's a new ID, this is our dispatched run!
                    if latest_id != previous_latest_id:
                        target_run_id = latest_id
                        print(f"🎯 Video Renderer: Locked onto target run ID: {target_run_id}")
                    else:
                        print("⏳ Video Renderer: Waiting for new run to appear on GitHub...")
                        time.sleep(15)
                        continue
                
                # Once we have the target run, find it in the list and poll its status
                target_run = next((r for r in runs if r.get('id') == target_run_id), None)
                if not target_run:
                    target_run = runs[0]
                    
                status = target_run.get('status')
                conclusion = target_run.get('conclusion')
                
                print(f"🤖 Video Renderer: Target Workflow status is '{status}' | conclusion is '{conclusion}'")
                
                if status == 'completed':
                    if conclusion == 'success':
                        print("✅ Video Renderer: Video successfully rendered on remote runner!")
                        return True
                    else:
                        print("❌ Video Renderer: Remote render failed!")
                        return False
        except Exception as e:
            print(f"⚠️ Video Renderer: Polling exception: {e}")
            
        time.sleep(30)
            
    print("⏰ Video Renderer: Polling timed out waiting for action to complete.")
    return False

def get_audio_duration(file_path: str) -> float:
    """
    Queries the exact playback duration of an audio file using ffprobe.
    """
    try:
        cmd = [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", file_path
        ]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
        return float(result.stdout.strip())
    except Exception as e:
        print(f"⚠️ Video Renderer: ffprobe failed for {file_path}: {e}. Defaulting to 6.0 seconds.")
        return 6.0

def build_ass_subtitles(script_lines: list, voice_paths: list) -> str:
    """
    Generates an ASS subtitle file styled with Montserrat-Bold, centralizing
    and timing progressive word appearances for YouTube Shorts.
    """
    ass_path = os.path.join(config.ASSETS_DIR, "subtitles.ass")
    print(f"✍️ Video Subtitles: Constructing ASS subtitles file at {ass_path}...")
    
    # Header definitions for ASS styled subtitle format
    content = [
        "[Script Info]",
        "ScriptType: v4.00+",
        "PlayResX: 1080",
        "PlayResY: 1920",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        "Style: Default,Montserrat-Bold,65,&H00FFFFFF,&H0000FFFF,&H00000000,&H00000000,1,0,0,0,100,100,0,0,1,3,3,2,10,10,200,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"
    ]
    
    current_time_offset = 0.0
    
    for idx, line in enumerate(script_lines):
        line_id = line["id"]
        text = line["text"]
        
        # Audio length query
        audio_file = voice_paths[idx] if idx < len(voice_paths) else ""
        duration = get_audio_duration(audio_file) if audio_file else 6.0
        
        words = text.split()
        if not words:
            current_time_offset += duration
            continue
            
        total_chars = sum(len(w) for w in words)
        if total_chars == 0:
            total_chars = 1
            
        # Distribute timing character-proportionally
        word_intervals = []
        accumulated = 0.0
        for w in words:
            w_len = len(w)
            w_dur = (w_len / total_chars) * duration
            start = current_time_offset + accumulated
            end = start + w_dur
            word_intervals.append((start, end, w))
            accumulated += w_dur
            
        # Group words in pairs for fast reading Shorts-style
        for i in range(0, len(word_intervals), 2):
            w_group = word_intervals[i:i+2]
            g_start = w_group[0][0]
            g_end = w_group[-1][1]
            g_text = " ".join([item[2] for item in w_group])
            
            # Convert float seconds to ASS timestamp (H:MM:SS.cs)
            def format_timestamp(sec):
                h = int(sec // 3600)
                m = int((sec % 3600) // 60)
                s = int(sec % 60)
                cs = int(round((sec % 1) * 100))
                if cs == 100:
                    cs = 99
                return f"{h}:{m:02d}:{s:02d}.{cs:02d}"
                
            start_str = format_timestamp(g_start)
            end_str = format_timestamp(g_end)
            
            # Highlight first word briefly with yellow or use fade-in
            ass_line = f"Dialogue: 0,{start_str},{end_str},Default,,0,0,0,,{{\\fad(80,80)}}{g_text}"
            content.append(ass_line)
            
        current_time_offset += duration
        
    with open(ass_path, "w", encoding="utf-8") as f:
        f.write("\n".join(content))
        
    print("✅ Video Subtitles: Subtitles ASS file written successfully.")
    return ass_path

def compile_whoosh_sound():
    """
    Downloads or programmatically ensures a whoosh sound transition effect exists in assets.
    """
    whoosh_path = os.path.join(config.ASSETS_DIR, "whoosh.mp3")
    if os.path.exists(whoosh_path) and os.path.getsize(whoosh_path) > 5000:
        return whoosh_path
        
    url = "https://freesound.org/data/previews/415/415209_5121236-lq.mp3"  # Standard small public whoosh
    try:
        print("🎵 Asset Builder: Downloading whoosh sound effect...")
        resp = requests.get(url, timeout=15)
        if resp.status_code == 200 and len(resp.content) > 5000:
            with open(whoosh_path, "wb") as f:
                f.write(resp.content)
            print("✅ Asset Builder: Whoosh sound effect saved.")
            return whoosh_path
    except Exception as e:
        print(f"⚠️ Asset Builder: Whoosh sound download failed: {e}. Generating silent fallback via FFmpeg...")
        
    # Generate a valid silent MP3 fallback via FFmpeg to prevent crashes
    try:
        subprocess.run([
            "ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:c=mono", "-t", "1",
            "-c:a", "libmp3lame", whoosh_path
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        print("✅ Asset Builder: Programmatically generated silent whoosh.mp3 fallback.")
    except Exception as fe:
        print(f"❌ Asset Builder: Failed to generate silent whoosh.mp3 fallback: {fe}")
    return whoosh_path

def compile_ding_sound():
    """
    Downloads or programmatically ensures a ding sound effect exists in assets.
    """
    ding_path = os.path.join(config.ASSETS_DIR, "ding.mp3")
    if os.path.exists(ding_path) and os.path.getsize(ding_path) > 5000:
        return ding_path
        
    url = "https://freesound.org/data/previews/338/338692_5739343-lq.mp3"  # Small public ding sound
    try:
        print("🎵 Asset Builder: Downloading ding sound effect...")
        resp = requests.get(url, timeout=15)
        if resp.status_code == 200 and len(resp.content) > 5000:
            with open(ding_path, "wb") as f:
                f.write(resp.content)
            print("✅ Asset Builder: Ding sound effect saved.")
            return ding_path
    except Exception as e:
        print(f"⚠️ Asset Builder: Ding sound download failed: {e}. Generating silent fallback via FFmpeg...")
        
    # Generate a valid silent MP3 fallback via FFmpeg to prevent crashes
    try:
        subprocess.run([
            "ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:c=mono", "-t", "1",
            "-c:a", "libmp3lame", ding_path
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        print("✅ Asset Builder: Programmatically generated silent ding.mp3 fallback.")
    except Exception as fe:
        print(f"❌ Asset Builder: Failed to generate silent ding.mp3 fallback: {fe}")
    return ding_path

def render_video(job_id: str):
    """
    EXECUTES INSIDE GITHUB RUNNER.
    1. Downloads assets.
    2. Sequentially compiles each scene into an MP4 with Ken Burns effect.
    3. Merges scene audios and applies sidechain ducking.
    4. Burns styled Montserrat word subtitles.
    5. Uploads resulting video back to Supabase.
    """
    print(f"🎬 Runner: Starting FFmpeg compilation for Job {job_id}...")
    
    try:
        # Step 1: Get job details from DB
        job = supabase_client.get_job(job_id)
        if not job:
            raise ValueError(f"Job {job_id} not found in database.")
            
        gemini_json = job.get("gemini_json")
        if not gemini_json:
            raise ValueError("No gemini_json data in job.")
            
        # Get voice settings
        settings = supabase_client.get_settings()
        voice_id = settings["voice_id"]
        
        # Ensure directories
        os.makedirs(config.ASSETS_DIR, exist_ok=True)
        os.makedirs(config.OUTPUT_DIR, exist_ok=True)
        
        # Download Assets
        print("🎬 Runner: Pulling visual resources...")
        bg_paths = asset_builder.generate_background_images(gemini_json["visual_prompts"])
        logo_path = asset_builder.fetch_brand_logo(gemini_json["brand_keyword"], gemini_json["brand_domain"])
        
        print("🎬 Runner: Generating voice narration...")
        # Asynchronously run TTS in the runner environment
        voice_paths = asyncio.run(asset_builder.generate_voiceover(gemini_json["script_lines"], voice_id))
        
        print("🎬 Runner: Fetching background score and sound effects...")
        bg_music_path = asset_builder.download_background_music()
        whoosh_path = compile_whoosh_sound()
        ding_path = compile_ding_sound()
        
        # Calculate matching scene ids
        script_lines = gemini_json["script_lines"]
        scene_files = []
        scene_audios = []
        
        # Detect logo insertion point: which script_line mentions brand_keyword?
        brand_keyword = gemini_json.get("brand_keyword", "").lower()
        logo_scene_idx = -1
        for idx, line in enumerate(script_lines):
            if brand_keyword in line["text"].lower():
                logo_scene_idx = idx
                break
        
        if logo_scene_idx == -1 and len(script_lines) > 0:
            logo_scene_idx = 0  # Fallback to first scene
            
        # Compile individual scenes
        print("🎬 Runner: Compiling individual scene video clips...")
        for i in range(len(script_lines)):
            scene_id = i + 1
            bg_img = bg_paths[i]
            voice_mp3 = voice_paths[i]
            
            # Query duration and frames
            duration = get_audio_duration(voice_mp3)
            frames = int(duration * 30)
            
            scene_output = os.path.join(config.ASSETS_DIR, f"scene_{scene_id}.mp4")
            
            # Step 1: Base scene rendering with Ken Burns zoom pan
            # Formula: min(zoom+0.0008,1.3)
            # Scaling, format and audio muxing
            cmd = [
                "ffmpeg", "-y", "-loop", "1", "-i", bg_img, "-i", voice_mp3,
                "-vf", f"zoompan=z='min(zoom+0.0008,1.3)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s=1080x1920",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
                "-shortest", scene_output
            ]
            print(f"🎬 Runner: Compiling Scene {scene_id} ({duration:.2f}s)...")
            subprocess.run(cmd, check=True)
            
            # Step 3: Logo and Ding Overlay if this is the logo scene and logo.png exists
            if i == logo_scene_idx and logo_path and os.path.exists(logo_path):
                print(f"🎬 Runner: Injecting Brand Logo overlay into Scene {scene_id}...")
                overlayed_scene = os.path.join(config.ASSETS_DIR, f"scene_logo_{scene_id}.mp4")
                
                # Apply 0.5s fade in and fade out on logo overlay
                fade_in_end = 0.5
                fade_out_start = duration - 0.5
                
                # Filtergraph: Overlay logo at W-120:H-250 (bottom-right area)
                logo_filter = (
                    f"[1:v]scale=80:80,fade=in:st=0:d=0.5:alpha=1,fade=out:st={fade_out_start}:d=0.5:alpha=1[logo];"
                    f"[0:v][logo]overlay=W-120:H-250[v]"
                )
                
                # Verify if ding path exists and is a valid audio file (>5000 bytes)
                has_ding = ding_path and os.path.exists(ding_path) and os.path.getsize(ding_path) > 5000
                
                if has_ding:
                    print(f"🎬 Runner: Mixing Ding sound effect into Scene {scene_id}...")
                    cmd_logo = [
                        "ffmpeg", "-y", "-i", scene_output, "-i", logo_path, "-i", ding_path,
                        "-filter_complex", logo_filter,
                        "-map", "[v]",
                        # Mix vocal audio and Ding together
                        "-filter_complex", "[0:a][2:a]amix=inputs=2:duration=first[a]",
                        "-map", "[a]",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", overlayed_scene
                    ]
                else:
                    print(f"⚠️ Runner: Ding sound is unavailable or invalid. Adding brand logo without audio overlay in Scene {scene_id}...")
                    cmd_logo = [
                        "ffmpeg", "-y", "-i", scene_output, "-i", logo_path,
                        "-filter_complex", logo_filter,
                        "-map", "[v]",
                        "-map", "0:a",  # keep original vocal audio unchanged
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "copy", overlayed_scene
                    ]
                subprocess.run(cmd_logo, check=True)
                scene_output = overlayed_scene
                
            scene_files.append(scene_output)
            scene_audios.append(voice_mp3)
            
        # Step 2: Concatenate all compiled scenes
        print("🎬 Runner: Concatenating scenes into unified timeline...")
        concat_txt = os.path.join(config.ASSETS_DIR, "concat.txt")
        with open(concat_txt, "w") as f:
            for filepath in scene_files:
                f.write(f"file '{os.path.abspath(filepath)}'\n")
                
        raw_concat_video = os.path.join(config.ASSETS_DIR, "raw_concat.mp4")
        subprocess.run([
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_txt,
            "-c", "copy", raw_concat_video
        ], check=True)
        
        # Assemble Voiceover Sequence
        voice_concat_txt = os.path.join(config.ASSETS_DIR, "voice_concat.txt")
        with open(voice_concat_txt, "w") as f:
            for filepath in scene_audios:
                f.write(f"file '{os.path.abspath(filepath)}'\n")
                
        concatenated_voice = os.path.join(config.ASSETS_DIR, "voice_concat.mp3")
        subprocess.run([
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", voice_concat_txt,
            "-c", "copy", concatenated_voice
        ], check=True)
        
        total_video_duration = get_audio_duration(concatenated_voice)
        print(f"🎬 Runner: Total compiled narration duration is {total_video_duration:.2f} seconds.")
        
        # Step 5: Sidechain Audio Compressor for ducking
        print("🎬 Runner: Mixing background music with vocal sidechain compressor ducking...")
        ducked_audio = os.path.join(config.ASSETS_DIR, "ducked_audio.mp3")
        
        # Check if background music is available and valid (>10000 bytes)
        has_bg_music = bg_music_path and os.path.exists(bg_music_path) and os.path.getsize(bg_music_path) > 10000
        
        if has_bg_music:
            # sidechaincompress sidechaining music [1:a] using vocal [0:a]
            # Threshold: 0.12, Duck volume: 0.15 voice, 0.80 silence
            audio_filter = (
                f"[1:a]aloop=loop=-1:size=2e+9[loop_music];"
                f"[loop_music]atrim=0:{total_video_duration}[music];"
                f"[music][0:a]sidechaincompress=threshold=0.12:ratio=15:attack=100:release=450[comp]"
            )
            cmd_audio = [
                "ffmpeg", "-y", "-i", concatenated_voice, "-i", bg_music_path,
                "-filter_complex", audio_filter, "-map", "[comp]",
                "-c:a", "libmp3lame", "-b:a", "192k", ducked_audio
            ]
            subprocess.run(cmd_audio, check=True)
        else:
            print("⚠️ Runner: Background music is unavailable or invalid. Using raw voice track without music ducking...")
            # Convert raw concatenated voice to ducked_audio format directly
            subprocess.run([
                "ffmpeg", "-y", "-i", concatenated_voice,
                "-c:a", "libmp3lame", "-b:a", "192k", ducked_audio
            ], check=True)
        
        # Combine final video and audio
        muxed_file = os.path.join(config.ASSETS_DIR, "muxed.mp4")
        subprocess.run([
            "ffmpeg", "-y", "-i", raw_concat_video, "-i", ducked_audio,
            "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-shortest", muxed_file
        ], check=True)
        
        # Step 7: Burn subtitle ASS file onto video
        subtitles_file = build_ass_subtitles(script_lines, voice_paths)
        final_video_path = os.path.join(config.OUTPUT_DIR, "final_video.mp4")
        
        print("🎬 Runner: Burning progressive ASS word subtitles onto final video clip...")
        
        # Need to escape ASS subtitles file path for FFmpeg format compatibility
        escaped_subs = subtitles_file.replace("\\", "/").replace(":", "\\:")
        
        subprocess.run([
            "ffmpeg", "-y", "-i", muxed_file,
            "-vf", f"subtitles={escaped_subs}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "copy", final_video_path
        ], check=True)
        
        print("🎉 Runner: Video rendered successfully! Uploading file to Supabase...")
        
        # Upload rendering back to Supabase
        filename = f"video_{job_id}.mp4"
        public_url = supabase_client.upload_video_to_storage(final_video_path, filename)
        
        # Update jobs table status and storage details
        supabase_client.update_job(
            job_id=job_id,
            status="done",
            video_url=public_url
        )
        print("🎉 Runner: Pipeline execution successfully terminated and saved.")
        
    except Exception as e:
        err_msg = f"Runner FFmpeg render pipeline failure: {e}"
        print(f"❌ {err_msg}")
        supabase_client.send_telegram_alert(err_msg)
        supabase_client.update_job(job_id=job_id, status="rejected", feedback=err_msg)
        sys.exit(1)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--job_id", help="The UUID of the job to render")
    args = parser.parse_args()
    
    if args.job_id:
        render_video(args.job_id)
    else:
        print("⚠️ Local Test mode: Pass --job_id <uuid> to run FFmpeg render compiler.")
