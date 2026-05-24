"""
Asset Builder module for the Video automation pipeline.
Downloads real Pexels stock VIDEOS for scene backgrounds using Gemini-generated search_keywords.
Fetches brand logos via Clearbit/Google, generates TTS voice via edge-tts, and downloads background music.
FFmpeg solid-color fallback is used only if Pexels Videos API fails for any scene.
"""

import os
import subprocess
import requests
import asyncio
import edge_tts
import config
import supabase_client


def _pexels_search_video(query: str, pexels_api_key: str) -> str | None:
    """
    Searches Pexels Videos API for a portrait video matching the query.
    Returns the HD video file URL (or best available), or None on failure.
    """
    try:
        headers = {"Authorization": pexels_api_key}
        params = {
            "query": query,
            "orientation": "portrait",
            "size": "large",
            "per_page": 5
        }
        resp = requests.get(
            "https://api.pexels.com/videos/search",
            headers=headers,
            params=params,
            timeout=15
        )
        if resp.status_code == 200:
            videos = resp.json().get("videos", [])
            if videos:
                video = videos[0]
                video_files = video.get("video_files", [])
                if not video_files:
                    return None
                # Prefer HD quality, fallback to first available
                hd_file = next((f for f in video_files if f.get("quality") == "hd"), None)
                chosen = hd_file if hd_file else video_files[0]
                return chosen.get("link")
        print(f"⚠️ Pexels Videos: Status {resp.status_code} for query '{query}'")
    except Exception as e:
        print(f"⚠️ Pexels Videos: Exception searching for '{query}': {e}")
    return None


def _stream_download_video(video_url: str, filepath: str) -> bool:
    """
    Downloads a video from URL using streaming to handle large files.
    Returns True on success, False on failure.
    """
    try:
        with requests.get(video_url, stream=True, timeout=60) as r:
            if r.status_code != 200:
                print(f"⚠️ Pexels Videos: Download returned status {r.status_code}.")
                return False
            with open(filepath, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
        # Verify file is not empty
        if os.path.exists(filepath) and os.path.getsize(filepath) > 50000:
            return True
        print(f"⚠️ Pexels Videos: Downloaded file is too small or missing.")
        return False
    except Exception as e:
        print(f"⚠️ Pexels Videos: Stream download exception: {e}")
        return False


def _ffmpeg_color_fallback(filepath: str) -> bool:
    """
    Generates a solid dark-blue video using FFmpeg as a last-resort fallback.
    Returns True on success, False on failure.
    """
    try:
        subprocess.run([
            "ffmpeg", "-y",
            "-f", "lavfi",
            "-i", "color=c=0x1a1a2e:size=1080x1920:rate=30",
            "-t", "10",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            filepath
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print(f"✅ Fallback: Generated solid color background video: {filepath}")
        return True
    except Exception as e:
        print(f"❌ Fallback: FFmpeg color video generation failed: {e}")
        return False


def fetch_scene_videos(visual_prompts: list, brand_keyword: str = "") -> list:
    """
    Downloads real Pexels portrait stock videos for every scene background.
    Uses the Gemini-generated 'search_keywords' field as the Pexels search query.

    Multi-level fallback per scene:
      Level 1 — Gemini's exact search_keywords (e.g., 'person using smartphone')
      Level 2 — First word of search_keywords only
      Level 3 — Brand keyword (video topic)
      Level 4 — 'technology' (universal last resort)
      Level 5 — FFmpeg solid dark color video (never fails, even if Pexels key is missing)

    Returns list of file paths [assets/bg_1.mp4, assets/bg_2.mp4, ...]
    Pipeline NEVER crashes due to a missing or invalid Pexels key.
    """
    print("🎬 Asset Builder: Fetching Pexels stock videos for all scenes...")
    saved_paths = []

    # Read PEXELS_API_KEY from environment
    pexels_api_key = os.environ.get("PEXELS_API_KEY", "").strip()
    if not pexels_api_key:
        print("⚠️ Asset Builder: PEXELS_API_KEY is not set or empty. Using FFmpeg color fallback for ALL scenes.")

    for idx, vp in enumerate(visual_prompts):
        prompt_id = vp["id"]
        # Use search_keywords field (new Gemini schema), fallback to prompt for old jobs
        search_keywords = vp.get("search_keywords", vp.get("prompt", "")).strip()
        filename = f"bg_{prompt_id}.mp4"
        filepath = os.path.join(config.ASSETS_DIR, filename)

        # Build smart keyword fallback chain — unique, non-empty, ordered
        first_word = search_keywords.split()[0] if search_keywords else ""
        raw_chain = [
            search_keywords,
            first_word,
            brand_keyword.strip() if brand_keyword else "",
            "technology",
        ]
        seen = set()
        unique_chain = []
        for kw in raw_chain:
            kw = kw.strip()
            if kw and kw.lower() not in seen:
                seen.add(kw.lower())
                unique_chain.append(kw)

        success = False

        # Only attempt Pexels search if we have a valid API key
        if pexels_api_key:
            for level, keyword in enumerate(unique_chain, start=1):
                print(f"🎬 Pexels Videos: Scene {prompt_id} — Level {level} search: '{keyword}'...")
                video_url = _pexels_search_video(keyword, pexels_api_key)
                if video_url:
                    downloaded = _stream_download_video(video_url, filepath)
                    if downloaded:
                        print(f"✅ Pexels Videos: Scene {prompt_id} downloaded (query: '{keyword}').")
                        saved_paths.append(filepath)
                        success = True
                        break
        else:
            print(f"⚠️ Pexels Videos: No API key — skipping search for scene {prompt_id}, using color fallback.")

        # FFmpeg color fallback — runs if Pexels failed OR key is missing — never crashes pipeline
        if not success:
            print(f"🎨 Asset Builder: Using FFmpeg dark color fallback for scene {prompt_id}...")
            ok = _ffmpeg_color_fallback(filepath)
            if ok:
                saved_paths.append(filepath)
            else:
                err_msg = f"Scene {prompt_id}: Pexels AND FFmpeg fallback both failed. Cannot continue."
                print(f"❌ Asset Builder: {err_msg}")
                supabase_client.send_telegram_alert(err_msg)
                raise RuntimeError(err_msg)

    print(f"✅ Asset Builder: All {len(saved_paths)} scene videos ready.")
    return saved_paths


def fetch_brand_logo(brand_keyword: str, brand_domain: str) -> str:
    """
    Retrieves the official brand logo using a robust 3-layer fallback.
    Returns the path to assets/logo.png on success, or None on failure.
    """
    print(f"🏷️ Asset Builder: Resolving brand logo for '{brand_keyword}' ({brand_domain})...")
    filepath = os.path.join(config.ASSETS_DIR, "logo.png")

    # Clean up previous logos if present
    if os.path.exists(filepath):
        try:
            os.remove(filepath)
        except:
            pass

    # ━━━ LAYER 1: Clearbit Logo API ━━━
    try:
        clearbit_url = f"https://logo.clearbit.com/{brand_domain}"
        print(f"🔌 Logo Layer 1: Querying Clearbit for {brand_domain}...")
        response = requests.get(clearbit_url, timeout=10)
        if response.status_code == 200 and len(response.content) > 1000:
            with open(filepath, "wb") as f:
                f.write(response.content)
            print(f"✅ Logo: Clearbit success for {brand_domain}")
            return filepath
        print(f"⚠️ Logo Layer 1: Clearbit returned bad code or small content for {brand_domain}.")
    except Exception as e:
        print(f"⚠️ Logo Layer 1: Clearbit exception: {e}")

    # ━━━ LAYER 2: Google S2 Favicon API ━━━
    try:
        favicon_url = f"https://www.google.com/s2/favicons?domain={brand_domain}&sz=256"
        print(f"🔌 Logo Layer 2: Querying Google Favicons for {brand_domain}...")
        response = requests.get(favicon_url, timeout=10)
        if response.status_code == 200 and len(response.content) > 500:
            with open(filepath, "wb") as f:
                f.write(response.content)
            print("✅ Logo: Google Favicon success")
            return filepath
        print(f"⚠️ Logo Layer 2: Google Favicon returned bad code or small content.")
    except Exception as e:
        print(f"⚠️ Logo Layer 2: Google Favicon exception: {e}")

    # ━━━ LAYER 3: DuckDuckGo Image Search ━━━
    try:
        if os.environ.get("GITHUB_ACTIONS") == "true":
            print("🔌 Logo Layer 3: Running inside GitHub Actions. Skipping DuckDuckGo search to prevent remote rate-limits/hangs.")
            return None

        print(f"🔌 Logo Layer 3: Querying DuckDuckGo Image search for '{brand_keyword}'...")
        from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            query = f"{brand_keyword} official logo PNG transparent background"
            results = list(ddgs.images(query, max_results=3))

            for index, result in enumerate(results):
                try:
                    img_url = result.get('image')
                    if not img_url:
                        continue
                    print(f"🔌 Logo Layer 3: Attempting result {index+1} download: {img_url}")
                    response = requests.get(img_url, timeout=10)
                    if response.status_code == 200 and len(response.content) > 1000:
                        with open(filepath, "wb") as f:
                            f.write(response.content)
                        print(f"✅ Logo: DuckDuckGo Image Search success!")
                        return filepath
                except Exception as inner_e:
                    print(f"⚠️ Logo Layer 3: Result {index+1} failed to download: {inner_e}")
                    continue
    except Exception as e:
        print(f"⚠️ Logo Layer 3: DuckDuckGo Search global exception: {e}")

    # ━━━ No Logo Found ━━━
    print("⚠️ Logo not found via any method. Skipping logo overlay in video.")
    return None


async def generate_voiceover(script_lines: list, voice_id: str) -> list:
    """
    Generates vocal voiceover audio files asynchronously using edge-tts.
    Saves outputs to assets/voice_{id}.mp3 and returns a list of paths.
    """
    print(f"🔊 Asset Builder: Commencing TTS generation using voice: {voice_id}...")
    saved_paths = []

    for line in script_lines:
        line_id = line["id"]
        text = line["text"]
        filename = f"voice_{line_id}.mp3"
        filepath = os.path.join(config.ASSETS_DIR, filename)

        try:
            print(f"🔊 Asset Builder: Speaking line {line_id}...")
            communicate = edge_tts.Communicate(text=text, voice=voice_id)
            await communicate.save(filepath)

            if os.path.exists(filepath) and os.path.getsize(filepath) > 100:
                print(f"✅ Asset Builder: TTS Voice saved: {filename}")
                saved_paths.append(filepath)
            else:
                raise ValueError("Generated file is missing or empty")

        except Exception as e:
            err_msg = f"Failed to generate TTS voiceover for line {line_id}: {e}"
            print(f"❌ Asset Builder: {err_msg}")
            supabase_client.send_telegram_alert(err_msg)

    return saved_paths


def download_background_music() -> str:
    """
    Downloads free background music track from Bensound and stores it locally.
    Provides failover print warnings if the external URL changes or times out.
    """
    print("🎵 Asset Builder: Fetching background music...")
    filepath = os.path.join(config.ASSETS_DIR, "bg_music.mp3")

    # If file exists, we don't need to re-download
    if os.path.exists(filepath) and os.path.getsize(filepath) > 50000:
        print("✅ Asset Builder: Background music already exists locally.")
        return filepath

    url = "https://www.bensound.com/bensound-music/bensound-ukulele.mp3"
    try:
        response = requests.get(url, timeout=30)
        if response.status_code == 200:
            with open(filepath, "wb") as f:
                f.write(response.content)
            print("✅ Asset Builder: Background music downloaded and saved.")
            return filepath
        else:
            print(f"⚠️ Asset Builder: Bensound music URL returned status {response.status_code}.")
    except Exception as e:
        print(f"⚠️ Asset Builder: Bensound music download failed: {e}.")

    print("⚠️ Asset Builder: Please place a royalty-free 'bg_music.mp3' in your assets/ folder if music overlay is required.")
    return filepath


if __name__ == "__main__":
    # Rapid local tests
    fetch_brand_logo("Google", "google.com")

    async def test_tts():
        lines = [{"id": 99, "text": "This is a beautiful test voiceover powered by edge tts."}]
        await generate_voiceover(lines, "en-US-AndrewNeural")
    asyncio.run(test_tts())
