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
            "job_id": str(job_id)
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

def check_github_action_status(job_id: str) -> bool:
    """
    Polls the GitHub Action run status every 30 seconds for up to 20 minutes.
    """
    print("⏳ Video Renderer: Commencing status polling...")
    url = f"https://api.github.com/repos/{GITHUB_REPO_OWNER}/{GITHUB_REPO_NAME}/actions/runs"
    
    headers = {
        "Authorization": f"Bearer {GITHUB_PAT}",
        "Accept": "application/vnd.github+json"
    }
    
    # Poll every 30 seconds, max 40 attempts = 20 minutes
    for attempt in range(40):
        print(f"⏳ Video Renderer: Polling attempt {attempt + 1}/40...")
        time.sleep(30)
        try:
            response = requests.get(url, headers=headers, timeout=20)
            if response.status_code != 200:
                print(f"⚠️ Video Renderer: Status API returned code {response.status_code}")
                continue
                
            runs = response.json().get('workflow_runs', [])
            if runs:
                latest = runs[0]
                status = latest.get('status')
                conclusion = latest.get('conclusion')
                
                print(f"🤖 Video Renderer: Remote Workflow status is '{status}' | conclusion is '{conclusion}'")
                
                if status == 'completed':
                    if conclusion == 'success':
                        print("✅ Video Renderer: Video successfully rendered on remote runner!")
                        return True
                    else:
                        print("❌ Video Renderer: Remote render failed!")
                        return False
        except Exception as e:
            print(f"⚠️ Video Renderer: Polling exception: {e}")
            
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
    if os.path.exists(whoosh_path) and os.path.getsize(whoosh_path) > 1000:
        return whoosh_path
        
    url = "https://freesound.org/data/previews/415/415209_5121236-lq.mp3"  # Standard small public whoosh
    try:
        print("🎵 Asset Builder: Downloading whoosh sound effect...")
        resp = requests.get(url, timeout=15)
        if resp.status_code == 200:
            with open(whoosh_path, "wb") as f:
                f.write(resp.content)
            print("✅ Asset Builder: Whoosh sound effect saved.")
            return whoosh_path
    except Exception as e:
        print(f"⚠️ Asset Builder: Whoosh sound download failed: {e}. Writing silent fallback.")
        
    # Write a silent fallback to avoid breaking FFmpeg
    with open(whoosh_path, "wb") as f:
        f.write(b"\x00" * 2000)
    return whoosh_path

def compile_ding_sound():
    """
    Downloads or programmatically ensures a ding sound effect exists in assets.
    """
    ding_path = os.path.join(config.ASSETS_DIR, "ding.mp3")
    if os.path.exists(ding_path) and os.path.getsize(ding_path) > 1000:
        return ding_path
        
    url = "https://freesound.org/data/previews/338/338692_5739343-lq.mp3"  # Small public ding sound
    try:
        print("🎵 Asset Builder: Downloading ding sound effect...")
        resp = requests.get(url, timeout=15)
        if resp.status_code == 200:
            with open(ding_path, "wb") as f:
                f.write(resp.content)
            print("✅ Asset Builder: Ding sound effect saved.")
            return ding_path
    except Exception as e:
        print(f"⚠️ Asset Builder: Ding sound download failed: {e}. Writing silent fallback.")
        
    # Write silent fallback
    with open(ding_path, "wb") as f:
        f.write(b"\x00" * 2000)
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
                print(f"🎬 Runner: Injecting Brand Logo and Ding audio into Scene {scene_id}...")
                overlayed_scene = os.path.join(config.ASSETS_DIR, f"scene_logo_{scene_id}.mp4")
                
                # Apply 0.5s fade in and fade out on logo overlay
                fade_in_end = 0.5
                fade_out_start = duration - 0.5
                
                # Filtergraph: Overlay logo at W-100:H-250 (bottom-right area)
                # Adds the Ding sound at st=0.1
                logo_filter = (
                    f"[1:v]scale=80:80,fade=in:st=0:d=0.5:alpha=1,fade=out:st={fade_out_start}:d=0.5:alpha=1[logo];"
                    f"[0:v][logo]overlay=W-120:H-250[v]"
                )
                
                cmd_logo = [
                    "ffmpeg", "-y", "-i", scene_output, "-i", logo_path, "-i", ding_path,
                    "-filter_complex", logo_filter,
                    "-map", "[v]",
                    # Mix vocal audio and Ding together
                    "-filter_complex", "[0:a][2:a]amix=inputs=2:duration=first[a]",
                    "-map", "[a]",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", overlayed_scene
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
        
        # sidechaincompress sidechaining music [1:a] using vocal [0:a]
        # Threshold: 0.15, Duck volume: 0.15 voice, 0.80 silence
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
