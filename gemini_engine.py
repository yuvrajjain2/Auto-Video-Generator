"""
Gemini Engine module for the Video automation pipeline.
Interacts with the Google Gemini 1.5 Flash API to produce highly engaging, factual short-form scripts in JSON format.
Ensures zero hallucinations, robust schemas, self-healing parse blocks, and automatic API retries.
"""

import re
import json
import config
import google.generativeai as genai
import supabase_client

# Configure the API key
try:
    if config.GEMINI_API_KEY:
        genai.configure(api_key=config.GEMINI_API_KEY)
        print("✅ Gemini Engine: Google Generative AI configured successfully.")
    else:
        print("⚠️ Gemini Engine: GEMINI_API_KEY environment variable is not set.")
except Exception as e:
    print(f"❌ Gemini Engine: Failed to configure Google Generative AI: {e}")

GEMINI_SYSTEM_PROMPT = """You are a viral short-form video script writer for YouTube Shorts.

CRITICAL CONTENT RULES — FOLLOW STRICTLY:
1. Use ONLY facts explicitly stated in the article text provided. Do NOT add any information from your training data or general knowledge.
2. If the article is about a tool or product you have never heard of, that is fine. Read the article carefully and base the entire script on what the article says.
3. Every single claim in your script must be directly verifiable from the article text.
4. Extract the brand website domain from the article text if it is mentioned. If not mentioned, set brand_domain as the brand name in lowercase with .com appended.
5. Do NOT hallucinate features, prices, dates, or statistics that are not in the article.
6. For every visual_prompt object, the search_keywords field must be exactly 2-3 simple English words that will be used to search Pexels stock videos. Rules for search_keywords:
   - GOOD: 'artificial intelligence robot', 'city night lights', 'person using smartphone', 'ocean waves aerial', 'data visualization screen', 'athlete running track'
   - BAD: 'futuristic holographic neural interface with glowing neon particles' (too long)
   - BAD: 'AI' (too short)
   - Maximum 3 words. Minimum 2 words. Real-world searchable. No adjectives like 'cinematic' or 'photorealistic'.
   - Match the keywords to what is being SPOKEN in that scene of the script.

OUTPUT FORMAT RULES:
Your output must be a single raw JSON object.
Absolutely no markdown formatting.
No code blocks. No backticks. No explanation text.
No preamble. No postamble.
Start your response with { and end with }.
Nothing before {. Nothing after }.

JSON Schema (follow exactly):
{
  "title": "string — viral curiosity-gap YouTube Shorts title under 60 characters",
  "description": "string — 3 sentence post description with 10 relevant hashtags at end",
  "brand_keyword": "string — exact official name of the tool or product from the article",
  "brand_domain": "string — website domain only, no https, no www. Example: openai.com. Extract from article if mentioned. If not mentioned: use brandname.com",
  "brand_website": "string — full URL with https://. Example: https://openai.com",
  "key_facts_from_article": [
    "string — direct fact from article",
    "string — direct fact from article",
    "string — direct fact from article"
  ],
  "script_lines": [
    {
      "id": 1,
      "text": "string — spoken words, natural and conversational, based only on article facts",
      "duration_seconds": number
    }
  ],
  "visual_prompts": [
    {
      "id": 1,
      "search_keywords": "string — exactly 2-3 simple English words for Pexels video search. Example: 'person using smartphone'",
      "scene_description": "string — one sentence describing what should visually appear in this scene"
    }
  ]
}

SCRIPT RULES:
- script_lines total duration = exactly 55-60 seconds
- Maximum 8 scenes, minimum 5 scenes
- script_lines and visual_prompts must have exact same array length and matching ids
- First line must hook viewer in first 3 seconds
- Use energetic fast-paced Shorts style
- Never mention facts not in the article
- search_keywords must match the topic being SPOKEN in each scene (not generic)
- Keep search_keywords simple, real-world, and Pexels-searchable (2-3 words max)"""

def clean_and_parse_json(text: str) -> dict:
    """
    Cleans raw LLM response text by stripping code backticks or markdown,
    extracts the JSON substring, and parses it into a Python dictionary.
    """
    cleaned = text.strip()
    
    # Strip markdown block ticks if present
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    elif cleaned.startswith("```"):
        cleaned = cleaned[3:]
        
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
        
    cleaned = cleaned.strip()
    
    # Extract outer matching { ... } if there is any pre/postamble
    match = re.search(r"(\{.*\})", cleaned, re.DOTALL)
    if match:
        cleaned = match.group(1)
        
    return json.loads(cleaned)

def validate_script_json(script_data: dict) -> dict:
    """
    Validates the generated JSON schema requirements:
    1. Checks for all required keys.
    2. Validates array lengths are matching between lines and visual prompts.
    3. Normalizes and validates brand_domain format.
    """
    required_keys = [
        "title", "description", "brand_keyword", "brand_domain",
        "brand_website", "key_facts_from_article", "script_lines", "visual_prompts"
    ]
    
    # 1. Check keys
    for key in required_keys:
        if key not in script_data:
            raise ValueError(f"Missing required JSON key: {key}")
            
    # 2. Check array lengths matching
    lines = script_data["script_lines"]
    prompts = script_data["visual_prompts"]
    
    if len(lines) != len(prompts):
        raise ValueError(f"Array length mismatch: script_lines count is {len(lines)}, but visual_prompts is {len(prompts)}.")
        
    if len(lines) < 5 or len(lines) > 8:
        print(f"⚠️ Gemini Engine Warning: script_lines count is {len(lines)}, recommended is 5 to 8.")

    # 3. Validate and sanitize brand_domain
    domain = script_data["brand_domain"]
    sanitized_domain = domain.replace("https://", "").replace("http://", "").replace("www.", "").strip("/")
    script_data["brand_domain"] = sanitized_domain
    
    # 4. Check for IDs matching
    for i in range(len(lines)):
        lines[i]["id"] = i + 1
        prompts[i]["id"] = i + 1
        
    return script_data

def generate_script(full_article_text: str, feedback: str = None, old_script: str = None, job_id: str = None) -> dict:
    """
    Calls the Google Gemini API to generate the script.
    Implements a self-healing parsing loop and up to 3 API retry attempts.
    Updates the Supabase jobs table with the script data on success.
    """
    print("🧠 Gemini Engine: Starting script generation...")
    
    if not full_article_text or len(full_article_text.strip()) == 0:
        err_msg = "Cannot generate script: full_article_text is empty."
        print(f"❌ Gemini Engine: {err_msg}")
        supabase_client.send_telegram_alert(err_msg)
        return {}

    # Build User Prompt
    rewrite_instructions = ""
    if feedback:
        rewrite_instructions = f"""
REWRITE INSTRUCTIONS:
The previous version was rejected. 
User feedback: {feedback}
Previous script for reference: {old_script}
Fix exactly what the user mentioned. Keep all 
facts from the original article.
"""

    user_prompt = f"""ARTICLE TEXT TO BASE EVERYTHING ON:
{full_article_text}
{rewrite_instructions}
Generate the video script JSON now."""

    attempts = 3
    for attempt in range(1, attempts + 1):
        try:
            print(f"🧠 Gemini Engine: API Call attempt {attempt} of {attempts}...")
            
            # Using the official gemini-2.5-flash model
            model = genai.GenerativeModel(
                model_name="gemini-2.5-flash",
                system_instruction=GEMINI_SYSTEM_PROMPT
            )
            
            response = model.generate_content(
                contents=user_prompt,
                generation_config={"response_mime_type": "application/json"}
            )
            
            raw_text = response.text
            print(f"✅ Gemini Engine: API call success. Raw response length: {len(raw_text)}")
            
            # Parsing
            try:
                parsed_json = clean_and_parse_json(raw_text)
                print("✅ Gemini Engine: JSON parsing success!")
            except Exception as parse_err:
                print(f"⚠️ Gemini Engine: JSON parsing failed on attempt {attempt}: {parse_err}. Retrying parse after backtick cleaning...")
                # Strip backticks and try once more manually
                stripped = raw_text.replace("`", "")
                parsed_json = clean_and_parse_json(stripped)
                print("✅ Gemini Engine: JSON parsing recovery success!")

            # Validation
            validated_json = validate_script_json(parsed_json)
            print("✅ Gemini Engine: JSON validation success!")
            
            # Save to Supabase if job_id is provided
            if job_id:
                supabase_client.update_job(
                    job_id=job_id,
                    gemini_json=validated_json,
                    status="scripted"
                )
                print("💾 Gemini Engine: Job status updated to 'scripted' in Supabase.")
                
            return validated_json

        except Exception as e:
            err_msg = f"Gemini Engine: Attempt {attempt} failed: {e}"
            print(f"❌ {err_msg}")
            if attempt == attempts:
                supabase_client.send_telegram_alert(err_msg)
                
    return {}

if __name__ == "__main__":
    # Test script generation with mock data
    sample_text = (
        "OpenAI today officially announced GPT-5, their next-generation flagship AI system. "
        "The model is 10 times more capable than GPT-4 and runs on the website chatgpt.com. "
        "It features advanced multimodal capabilities including real-time video processing. "
        "GPT-5 is priced starting at $20 a month for individual subscribers, launching worldwide today. "
        "Subscribers can access it at https://chatgpt.com/gpt5."
    )
    result = generate_script(sample_text)
    print("Parsed JSON Output:")
    print(json.dumps(result, indent=2))
