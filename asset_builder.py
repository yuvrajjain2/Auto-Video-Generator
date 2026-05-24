"""
Asset Builder module for the Video automation pipeline.
Downloads real Pexels stock photos for scene backgrounds using Gemini-generated search queries.
Fetches brand logos via Clearbit/Google, generates TTS voice via edge-tts, and downloads background music.
"""

import os
import requests
import asyncio
import edge_tts
from PIL import Image
import config
import supabase_client

def _pexels_fetch_photo(query: str, pexels_api_key: str) -> str | None:
    """
    Searches Pexels for a portrait photo matching the query.
    Returns the highest-quality image URL, or None on failure.
    """
    try:
        headers = {"Authorization": pexels_api_key}
        params = {
            "query": query,
            "orientation": "portrait",
            "size": "large",
            "per_page": 5
        }
        resp = requests.get("https://api.pexels.com/v1/search", headers=headers, params=params, timeout=15)
        if resp.status_code == 200:
            photos = resp.json().get("photos", [])
            if photos:
                photo = photos[0]
                return photo["src"].get("large2x") or photo["src"].get("large")
        print(f"⚠️ Pexels: Status {resp.status_code} for query '{query}'")
    except Exception as e:
        print(f"⚠️ Pexels: Exception searching for '{query}': {e}")
    return None


def _download_and_crop_pexels_photo(photo_url: str, filepath: str) -> bool:
    """
    Downloads a Pexels photo URL and center-crops + resizes it to 1080x1920 portrait.
    Returns True on success, False on failure.
    """
    try:
        from io import BytesIO
        img_resp = requests.get(photo_url, timeout=25)
        if img_resp.status_code != 200 or len(img_resp.content) < 10000:
            print(f"⚠️ Pexels: Download returned bad status {img_resp.status_code} or file too small.")
            return False
        pil_img = Image.open(BytesIO(img_resp.content)).convert("RGB")
        orig_w, orig_h = pil_img.size
        target_ratio = 1080 / 1920  # 9:16 portrait
        orig_ratio = orig_w / orig_h
        if orig_ratio > target_ratio:
            # Wider than 9:16 → crop sides
            new_w = int(orig_h * target_ratio)
            left = (orig_w - new_w) // 2
            pil_img = pil_img.crop((left, 0, left + new_w, orig_h))
        else:
            # Taller than 9:16 → crop top/bottom
            new_h = int(orig_w / target_ratio)
            top = (orig_h - new_h) // 2
            pil_img = pil_img.crop((0, top, orig_w, top + new_h))
        pil_img = pil_img.resize((1080, 1920), Image.LANCZOS)
        pil_img.save(filepath, "JPEG", quality=92)
        return True
    except Exception as e:
        print(f"⚠️ Pexels: Image processing exception: {e}")
        return False


def generate_background_images(visual_prompts: list, brand_keyword: str = "") -> list:
    """
    Downloads real 1080x1920 stock photos from Pexels for every scene.
    Uses the Gemini-generated 'prompt' field directly as the Pexels search query.
    Multi-level fallback strategy:
      1. Gemini's exact pexels query (e.g., 'ocean waves aerial')
      2. First single word of the query
      3. Brand keyword (topic of the video)
      4. 'technology' (universal last resort)
    Raises RuntimeError if ANY scene fails — no silent black screens.
    """
    print("🎨 Asset Builder: Fetching Pexels stock photos for all scenes...")
    saved_paths = []

    pexels_api_key = config.PEXELS_API_KEY
    if not pexels_api_key:
        raise RuntimeError("PEXELS_API_KEY is not set. Cannot generate scene backgrounds.")

    for idx, vp in enumerate(visual_prompts):
        prompt_id = vp["id"]
        # Gemini now generates prompt as a Pexels search query directly
        pexels_query = vp.get("prompt", "").strip()
        filename = f"bg_{prompt_id}.jpg"
        filepath = os.path.join(config.ASSETS_DIR, filename)

        # Build a smart fallback chain for this scene
        first_word = pexels_query.split()[0] if pexels_query else ""
        search_chain = [
            pexels_query,                           # Level 1: Gemini's exact Pexels query
            first_word,                             # Level 2: First keyword only
            brand_keyword if brand_keyword else "",  # Level 3: Video topic/brand
            "technology",                           # Level 4: Universal fallback
        ]
        # Remove duplicates and empty strings while preserving order
        seen = set()
        unique_chain = []
        for kw in search_chain:
            kw = kw.strip()
            if kw and kw.lower() not in seen:
                seen.add(kw.lower())
                unique_chain.append(kw)

        success = False
        for level, keyword in enumerate(unique_chain, start=1):
            print(f"🖼️ Pexels: Scene {prompt_id} — Level {level} search: '{keyword}'...")
            photo_url = _pexels_fetch_photo(keyword, pexels_api_key)
            if photo_url:
                downloaded = _download_and_crop_pexels_photo(photo_url, filepath)
                if downloaded:
                    print(f"✅ Pexels: Scene {prompt_id} saved (query: '{keyword}').")
                    saved_paths.append(filepath)
                    success = True
                    break

        if not success:
            err_msg = f"Pexels: ALL search levels failed for scene {prompt_id}. Cannot generate background."
            print(f"❌ Asset Builder: {err_msg}")
            supabase_client.send_telegram_alert(err_msg)
            raise RuntimeError(err_msg)

    print(f"✅ Asset Builder: All {len(saved_paths)} scene backgrounds fetched from Pexels.")
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
