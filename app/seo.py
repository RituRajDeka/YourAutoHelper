import json
import logging
from typing import Dict, List, Any
from pydantic import BaseModel, Field
from . import omniroute

logger = logging.getLogger("ai_video_clipper.seo")

class SEOMetadata(BaseModel):
    title: str = Field(description="Optimized, catchy YouTube Short title (max 60 chars, with emojis)")
    description: str = Field(description="Engaging description containing relevant keywords and 3-5 hashtags")
    tags: List[str] = Field(description="List of 5 to 10 highly relevant search tags/keywords")

def generate_metadata(transcript: dict, original_title: str) -> Dict[str, Any]:
    """
    Generates YouTube-optimized title, description, and tags based on the video transcript.
    """
    logger.info("Generating SEO metadata for video: %s", original_title)
    
    words = transcript.get("words", [])
    if words:
        # Construct plain text transcript (limit size)
        transcript_text = " ".join([w['word'] for w in words[:400]])
    else:
        transcript_text = "No speech detected in transcript."
        
    system_prompt = """You are a YouTube SEO expert specializing in viral video metadata optimization.
Your job is to generate highly engaging, search-optimized metadata for a YouTube Short based on a video's transcript and original title.

The output MUST be a valid JSON object matching this structure:
{
  "title": "catchy title with emojis",
  "description": "description with keywords and hashtags",
  "tags": ["tag1", "tag2", ...]
}

Guidelines:
- Title must be engaging, use emojis, and fit under 60 characters.
- Description must summarize the video, encourage views/likes, and include 3-5 viral hashtags (e.g., #shorts, #viral).
- Tags must be relevant search keywords that users type into YouTube.
- Do not output any preamble or comments, just the raw JSON."""

    user_prompt = f"""Original Video Title: {original_title}
Transcript snippet: {transcript_text}

Generate the SEO metadata now:"""

    try:
        seo_meta = omniroute.completion(
            prompt=user_prompt,
            response_model=SEOMetadata,
            system_prompt=system_prompt,
            temperature=0.7
        )
        logger.info("Successfully generated SEO metadata via OmniRoute.")
        return seo_meta.model_dump()
    except Exception as e:
        logger.error("Failed to generate SEO metadata via OmniRoute: %s. Using fallback metadata.", e)
        # Fallback metadata
        clean_title = original_title[:50] + " #shorts"
        return {
            "title": clean_title,
            "description": f"Check out this highlight from {original_title}! #shorts #viral",
            "tags": ["shorts", "viral", "trending", "highlight"]
        }
