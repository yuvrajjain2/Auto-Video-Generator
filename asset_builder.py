"""
Asset Builder module for the Video automation pipeline.
Generates or downloads all required physical media (background images, brand logos, TTS voices, background music) locally.
Ensures robust retry rules, image failovers with Pillow, and a rigorous 3-layer logo API search.
"""

import os
import urllib.parse
import requests
import asyncio
import edge_tts
from PIL import Image
import config
import supabase_client

def generate_background_images(visual_prompts: list) -> list:
    """
    Downloads custom 1080x1920 background images from Pollinations AI.
    If image download fails after 3 attempts, generates a local solid black failover image.
    """
    print("🎨 Asset Builder: Generating background images...")
    saved_paths = []
    import time
    import random
    
    for idx, vp in enumerate(visual_prompts):
        prompt_id = vp["id"]
        prompt_text = vp["prompt"]
        filename = f"bg_{prompt_id}.jpg"
        filepath = os.path.join(config.ASSETS_DIR, filename)
        
        # Avoid rate limiting by introducing a 4-second sleep between requests
        if idx > 0:
            print("🎨 Asset Builder: Sleeping 4 seconds before next Pollinations AI image generation to avoid rate limits...")
            time.sleep(4)
            
        encoded_prompt = urllib.parse.quote(prompt_text)
        seed = random.randint(1, 999999)
        url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1080&height=1920&nologo=true&seed={seed}"
        
        success = False
        print(f"🎨 Asset Builder: Downloading image {prompt_id} from Pollinations AI...")
        
        # 3 Retry Attempts
        for attempt in range(1, 4):
            try:
                response = requests.get(url, timeout=20)
                if response.status_code == 200 and len(response.content) > 10000:
                    with open(filepath, "wb") as f:
                        f.write(response.content)
                    print(f"✅ Asset Builder: Saved bg_{prompt_id}.jpg successfully on attempt {attempt}.")
                    saved_paths.append(filepath)
                    success = True
                    break
                else:
                    print(f"⚠️ Asset Builder: Attempt {attempt} for image {prompt_id} returned empty or bad status: {response.status_code}")
            except Exception as e:
                print(f"⚠️ Asset Builder: Attempt {attempt} for image {prompt_id} raised exception: {e}")
        
        # Fallback to Premium Dark Tech Gradient Canvas via Pillow
        if not success:
            print(f"❌ Asset Builder: Failed to download image {prompt_id} after 3 attempts. Utilizing premium tech gradient fallback.")
            try:
                # Create a stunning vertical gradient (Dark Purple/Violet to Dark Tech Blue)
                img = Image.new('RGB', (1080, 1920))
                for y in range(1920):
                    ratio = y / 1920.0
                    r = int(32 * (1 - ratio) + 12 * ratio)
                    g = int(12 * (1 - ratio) + 24 * ratio)
                    b = int(52 * (1 - ratio) + 56 * ratio)
                    img.paste((r, g, b), [0, y, 1080, y + 1])
                img.save(filepath, "JPEG")
                print(f"✅ Asset Builder: Programmatically created premium dark gradient background: {filepath}")
                saved_paths.append(filepath)
            except Exception as pillow_err:
                # Basic black fallback as absolute last resort
                try:
                    img = Image.new('RGB', (1080, 1920), color='black')
                    img.save(filepath, "JPEG")
                    saved_paths.append(filepath)
                except Exception as ex:
                    err_msg = f"Failed to create Pillow gradient and black fallbacks: {ex}"
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
