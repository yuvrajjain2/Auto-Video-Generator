"""
Scraper module for the Video automation pipeline.
Fetches top articles from tech RSS feeds and performs full article text scraping using newspaper3k or BeautifulSoup fallback.
"""

import requests
import feedparser
from bs4 import BeautifulSoup
from newspaper import Article as NewsArticle
import supabase_client

# Define the source feeds
RSS_FEEDS = [
    "https://feeds.feedburner.com/TechCrunch",
    "https://www.theverge.com/rss/index.xml",
    "https://feeds.arstechnica.com/arstechnica/index"
]

def scrape_full_text(url: str) -> str:
    """
    Crawls and extracts the full body text of an article using newspaper3k.
    If it fails, falls back to a custom BeautifulSoup extraction.
    """
    print(f"🕷️ Scraper: Scraping full text from: {url}")
    try:
        # Layer 1: newspaper3k
        news = NewsArticle(url)
        news.download()
        news.parse()
        full_text = news.text
        if full_text and len(full_text.split()) > 100:
            print(f"✅ Scraper: newspaper3k success ({len(full_text.split())} words)")
            return full_text
        else:
            print("⚠️ Scraper: newspaper3k extracted insufficient text. Trying Layer 2 fallback...")
    except Exception as e:
        print(f"⚠️ Scraper: newspaper3k failed for {url}: {e}. Trying Layer 2 fallback...")

    # Layer 2: Requests + BeautifulSoup fallback
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Verge, TechCrunch, and Ars Technica put their content inside paragraphs.
        # Find paragraphs inside article body tag or extract all paragraphs.
        paragraphs = soup.find_all('p')
        full_text = ' '.join([p.get_text() for p in paragraphs])
        
        # Clean extra whitespaces
        full_text = " ".join(full_text.split())
        
        if full_text and len(full_text.split()) > 100:
            print(f"✅ Scraper: requests+BS4 fallback success ({len(full_text.split())} words)")
            return full_text
        else:
            print("❌ Scraper: Fallback extraction resulted in empty text.")
            return ""
            
    except Exception as e:
        err_msg = f"Failed to scrape article from url {url}: {e}"
        print(f"❌ Scraper: {err_msg}")
        # Send Telegram alert for scraping issues
        supabase_client.send_telegram_alert(err_msg)
        return ""

def fetch_trending_topics():
    """
    Orchestrates the entire scraping flow:
    1. Fetches top 2 articles from 3 RSS feeds.
    2. Scrapes the full-text content of each article.
    3. Filters out short articles (<300 words).
    4. Takes the top 5 articles, inserts them into Supabase, and returns the list of dicts.
    """
    print("📰 Scraper: Starting fetch_trending_topics process...")
    scraped_articles = []
    
    try:
        # Step 1: Fetch feed entries
        for feed_url in RSS_FEEDS:
            try:
                print(f"📡 Scraper: Parsing feed: {feed_url}")
                feed = feedparser.parse(feed_url)
                entries = feed.entries[:2]  # Top 2 entries per feed
                
                for entry in entries:
                    title = getattr(entry, "title", "No Title")
                    link = getattr(entry, "link", "")
                    
                    if not link:
                        continue
                    
                    scraped_articles.append({
                        "title": title,
                        "url": link
                    })
            except Exception as e:
                err_msg = f"Failed to parse RSS feed {feed_url}: {e}"
                print(f"❌ Scraper: {err_msg}")
                supabase_client.send_telegram_alert(err_msg)
        
        print(f"📋 Scraper: Found {len(scraped_articles)} candidate articles across feeds.")
        
        # Step 2: Scrape full text for each candidate
        final_articles = []
        for art in scraped_articles:
            full_text = scrape_full_text(art["url"])
            word_count = len(full_text.split()) if full_text else 0
            
            # Step 3: Filter for > 300 words
            if word_count > 300:
                final_articles.append({
                    "title": art["title"],
                    "url": art["url"],
                    "full_text": full_text,
                    "word_count": word_count
                })
            else:
                print(f"🗑️ Scraper: Filtering out '{art['title'][:30]}' due to word count ({word_count})")
            
            # Stop if we already have 5 high quality articles
            if len(final_articles) >= 5:
                break
                
        print(f"✅ Scraper: Final filtered list contains {len(final_articles)} articles.")
        
        # Step 4-5: Insert into Supabase with 'scraped' status
        saved_jobs = []
        for art in final_articles:
            job = supabase_client.insert_job(
                topic=art["title"],
                article_url=art["url"],
                full_article_text=art["full_text"]
            )
            if job:
                saved_jobs.append(job)
                
        print(f"🎉 Scraper: Successfully scraped and stored {len(saved_jobs)} jobs in database.")
        return final_articles

    except Exception as e:
        err_msg = f"Global error in fetch_trending_topics: {e}"
        print(f"❌ Scraper: {err_msg}")
        supabase_client.send_telegram_alert(err_msg)
        return []

if __name__ == "__main__":
    fetch_trending_topics()
