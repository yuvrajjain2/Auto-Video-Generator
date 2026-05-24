"""
Asset Builder module for the Video automation pipeline.
Downloads Pexels stock photos for scene backgrounds, fetches brand logos via Clearbit/Google,
generates TTS voice narration via edge-tts, and downloads background music.
Pillow gradient is used as a guaranteed fallback if Pexels fails.
"""

import os
import requests
import asyncio
import edge_tts
from PIL import Image
import config
import supabase_client

def _pexels_search_photo(keyword: str, pexels_api_key: str, orientation: str = "portrait") -> str | None:
    """
    Searches Pexels for a photo matching the keyword.
    Returns the best-quality portrait image URL, or None on failure.
    """
    try:
        headers = {"Authorization": pexels_api_key}
        params = {
            "query": keyword,
            "orientation": orientation,
            "size": "large",
            "per_page": 5
        }
        resp = requests.get("https://api.pexels.com/v1/search", headers=headers, params=params, timeout=15)
        if resp.status_code == 200:
            photos = resp.json().get("photos", [])
            if photos:
                # Pick the portrait photo with best resolution
                photo = photos[0]
                # Use large2x for highest quality, fallback to large
                return photo["src"].get("large2x") or photo["src"].get("large")
        print(f"⚠️ Pexels: Status {resp.status_code} for query '{keyword}'")
    except Exception as e:
        print(f"⚠️ Pexels: Exception searching for '{keyword}': {e}")
    return None


def _make_gradient_fallback(filepath: str, palette_idx: int):
    """
    Generates a cinematic dark gradient image using Pillow as a reliable fallback.
    """
    GRADIENT_PALETTES = [
        ((15, 10, 40),  (60, 20, 100)),   # Deep Violet → Purple
        ((5, 15, 50),   (20, 80, 140)),   # Midnight Blue → Ocean Blue
        ((40, 5, 20),   (110, 20, 60)),   # Dark Crimson → Magenta
        ((5, 35, 30),   (10, 90, 70)),    # Dark Forest → Emerald
        ((40, 20, 5),   (120, 60, 10)),   # Dark Bronze → Amber
        ((10, 10, 60),  (50, 10, 90)),    # Indigo → Royal Purple
        ((35, 5, 35),   (90, 15, 90)),    # Deep Plum → Orchid
        ((5, 40, 50),   (10, 100, 120)),  # Dark Teal → Cyan
        ((50, 10, 10),  (140, 40, 20)),   # Dark Ruby → Coral
        ((20, 20, 20),  (60, 60, 80)),    # Charcoal → Slate
    ]
    top_rgb, bot_rgb = GRADIENT_PALETTES[palette_idx % len(GRADIENT_PALETTES)]
    img = Image.new('RGB', (1080, 1920))
    pixels = img.load()
    for y in range(1920):
        ratio = y / 1919.0
        r = int(top_rgb[0] * (1 - ratio) + bot_rgb[0] * ratio)
        g = int(top_rgb[1] * (1 - ratio) + bot_rgb[1] * ratio)
        b = int(top_rgb[2] * (1 - ratio) + bot_rgb[2] * ratio)
        for x in range(1080):
            pixels[x, y] = (r, g, b)
    img.save(filepath, "JPEG", quality=95)


def generate_background_images(visual_prompts: list) -> list:
    """
    Downloads real 1080x1920 stock photos from Pexels for each scene background.
    Uses the scene's visual prompt keywords to search for a matching photo.
    Falls back to a local Pillow gradient if Pexels fails — guarantees no black screens.
    """
    print("🎨 Asset Builder: Fetching Pexels stock photos for scene backgrounds...")
    saved_paths = []

    pexels_api_key = config.PEXELS_API_KEY
    if not pexels_api_key:
        print("⚠️ Asset Builder: PEXELS_API_KEY not set — using Pillow gradient fallback for all scenes.")

    for idx, vp in enumerate(visual_prompts):
        prompt_id = vp["id"]
        prompt_text = vp.get("prompt", "")
        filename = f"bg_{prompt_id}.jpg"
        filepath = os.path.join(config.ASSETS_DIR, filename)

        success = False

        # ━━━ LAYER 1: Pexels API ━━━
        if pexels_api_key:
            # Extract a clean 2-3 word keyword from the prompt for better Pexels results
            # Take first 4 words of the prompt as the search keyword
            keyword_words = prompt_text.strip().split()[:4]
            keyword = " ".join(keyword_words) if keyword_words else "cinematic"
            print(f"🖼️ Pexels: Searching for scene {prompt_id} → '{keyword}'...")

            photo_url = _pexels_search_photo(keyword, pexels_api_key)

            # If no result, try a shorter 2-word fallback keyword
            if not photo_url and len(keyword_words) > 2:
                short_keyword = " ".join(keyword_words[:2])
                print(f"🖼️ Pexels: Retrying with shorter keyword → '{short_keyword}'...")
                photo_url = _pexels_search_photo(short_keyword, pexels_api_key)

            if photo_url:
                try:
                    img_resp = requests.get(photo_url, timeout=20)
                    if img_resp.status_code == 200 and len(img_resp.content) > 10000:
                        # Download and crop/resize to exact 1080x1920 portrait
                        from io import BytesIO
                        pil_img = Image.open(BytesIO(img_resp.content)).convert("RGB")
                        # Smart crop: center-crop to 9:16 ratio then resize
                        orig_w, orig_h = pil_img.size
                        target_ratio = 1080 / 1920  # 9:16
                        orig_ratio = orig_w / orig_h
                        if orig_ratio > target_ratio:
                            # Image is wider → crop sides
                            new_w = int(orig_h * target_ratio)
                            left = (orig_w - new_w) // 2
                            pil_img = pil_img.crop((left, 0, left + new_w, orig_h))
                        else:
                            # Image is taller → crop top/bottom
                            new_h = int(orig_w / target_ratio)
                            top = (orig_h - new_h) // 2
                            pil_img = pil_img.crop((0, top, orig_w, top + new_h))
                        pil_img = pil_img.resize((1080, 1920), Image.LANCZOS)
                        pil_img.save(filepath, "JPEG", quality=92)
                        print(f"✅ Pexels: Saved scene {prompt_id} background from Pexels stock photo.")
                        saved_paths.append(filepath)
                        success = True
                    else:
                        print(f"⚠️ Pexels: Photo download returned bad status or small file for scene {prompt_id}.")
                except Exception as e:
                    print(f"⚠️ Pexels: Photo processing failed for scene {prompt_id}: {e}")

        # ━━━ LAYER 2: Pillow Gradient Fallback ━━━
        if not success:
            print(f"🎨 Asset Builder: Using Pillow gradient fallback for scene {prompt_id} (palette {(idx % 10) + 1})...")
            try:
                _make_gradient_fallback(filepath, idx)
                print(f"✅ Asset Builder: Gradient fallback saved for scene {prompt_id}.")
                saved_paths.append(filepath)
            except Exception as e:
                err_msg = f"Failed to generate gradient fallback for scene {prompt_id}: {e}"
                print(f"❌ Asset Builder: {err_msg}")
                supabase_client.send_telegram_alert(err_msg)

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
            print(f"⚠️ Asset Builder: Bensound music URL returned status {response.status_code}. Using fallback method.")
    except Exception as e:
        print(f"⚠️ Asset Builder: Bensound music download failed: {e}. Using fallback method.")
        
    # Provide fallbacks: Create a placeholder silence/sine wave if needed or print instructions to copy one
    print("⚠️ Asset Builder: Please place a royalty-free 'bg_music.mp3' in your assets/ folder if music overlay is required.")
    return filepath

if __name__ == "__main__":
    # Rapid local tests
    # 1. Test logo resolver
    fetch_brand_logo("Google", "google.com")
    
    # 2. Test TTS voice output
    async def test_tts():
        lines = [{"id": 99, "text": "This is a beautiful test voiceover powered by edge tts."}]
        await generate_voiceover(lines, "en-US-AndrewNeural")
    asyncio.run(test_tts())
