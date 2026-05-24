"""
Configuration management module for the Video automation pipeline.
Loads variables from .env and os.environ, ensuring all folders are present and validated.
"""

import os
import logging
from pathlib import Path

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("config")

def load_env_file():
    """
    Manually parses the .env file in the same directory as config.py
    and populates os.environ if it exists. This avoids requiring python-dotenv.
    """
    try:
        env_path = Path(__file__).parent.resolve() / ".env"
        if env_path.exists():
            print(f"⚙️ Config: Found .env file at {env_path}. Loading environment variables...")
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" in line:
                        key, value = line.split("=", 1)
                        key = key.strip()
                        value = value.strip().strip("'\"")
                        os.environ[key] = value
            print("⚙️ Config: .env file parsed successfully.")
        else:
            print("⚠️ Config: .env file not found. Relying on system environment variables.")
    except Exception as e:
        print(f"❌ Config: Error reading .env file: {e}")

# Trigger loading of .env
load_env_file()

# Directories
BASE_DIR = Path(__file__).parent.resolve()
ASSETS_DIR = BASE_DIR / "assets"
OUTPUT_DIR = BASE_DIR / "output"

# Ensure dirs exist
try:
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"📁 Config: Asset and Output directories verified at:\n  - {ASSETS_DIR}\n  - {OUTPUT_DIR}")
except Exception as e:
    print(f"❌ Config: Failed to create directories: {e}")

# Secrets loading
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
YOUTUBE_CLIENT_ID = os.environ.get("YOUTUBE_CLIENT_ID")
YOUTUBE_CLIENT_SECRET = os.environ.get("YOUTUBE_CLIENT_SECRET")
YOUTUBE_REFRESH_TOKEN = os.environ.get("YOUTUBE_REFRESH_TOKEN")
GITHUB_PAT = os.environ.get("GITHUB_PAT")
GITHUB_REPO_OWNER = os.environ.get("GITHUB_REPO_OWNER")
GITHUB_REPO_NAME = os.environ.get("GITHUB_REPO_NAME")

def validate_config():
    """
    Validates that all essential environment variables are set.
    Logs warning for any missing environment keys.
    """
    required_keys = {
        "SUPABASE_URL": SUPABASE_URL,
        "SUPABASE_KEY": SUPABASE_KEY,
        "TELEGRAM_BOT_TOKEN": TELEGRAM_BOT_TOKEN,
        "TELEGRAM_CHAT_ID": TELEGRAM_CHAT_ID,
        "GEMINI_API_KEY": GEMINI_API_KEY,
        "YOUTUBE_CLIENT_ID": YOUTUBE_CLIENT_ID,
        "YOUTUBE_CLIENT_SECRET": YOUTUBE_CLIENT_SECRET,
        "YOUTUBE_REFRESH_TOKEN": YOUTUBE_REFRESH_TOKEN,
        "GITHUB_PAT": GITHUB_PAT,
        "GITHUB_REPO_OWNER": GITHUB_REPO_OWNER,
        "GITHUB_REPO_NAME": GITHUB_REPO_NAME
    }
    
    missing = [k for k, v in required_keys.items() if not v]
    if missing:
        print(f"⚠️ Config Warning: The following environment variables are missing: {', '.join(missing)}")
        return False
    
    print("✅ Config: All environment variables successfully loaded!")
    return True

# Run validation on import to catch errors early
validate_config()
