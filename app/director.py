"""
AI Editing Director — Translates user intent + video transcript into an EditPlan.

Two entry points:

  generate_edit_plan(transcript, video_title)
      Auto-mode: the AI picks the best Short-worthy segment on its own.

  generate_edit_plan_from_prompt(user_prompt, transcript, video_title)
      Prompt-driven mode: the user supplies natural-language editing directions.

Both use OmniRoute for routing and return a validated EditPlan instance.
"""
from __future__ import annotations

import logging
from typing import Optional

from .edit_plan import Cut, EditPlan, Zoom
from . import omniroute

logger = logging.getLogger("ai_video_clipper.director")

# ---------------------------------------------------------------------------
_EDIT_PLAN_SCHEMA = """
{
  "cuts":          [{"start_time": float, "end_time": float}],
  "zooms":         [{"time": float, "duration": float, "scale": float, "x": float, "y": float}],
  "speed_changes": [{"start_time": float, "end_time": float, "speed": float}],
  "transitions":   [{"cut_index": int, "type": "fade", "duration": float}],
  "sound_effects": [{"time": float, "name": str, "volume": float}],
  "music_changes": [],
  "emphasis_points": [],
  "caption_preferences": null
}
"""

_SYSTEM_BASE = f"""You are a world-class professional video editor specialising in viral YouTube Shorts.
Read the timestamped transcript and generate a structured EditPlan in JSON format.

SCHEMA:{_EDIT_PLAN_SCHEMA}

RULES:
- All times are relative to the ORIGINAL SOURCE VIDEO timeline.
- Every time must fall strictly within one of the cuts you define.
- Output ONLY the raw JSON object — no markdown fences, no prose.
"""


def _compact_transcript(transcript: dict, max_blocks: int = 150) -> str:
    words = transcript.get("words", [])
    if not words:
        return "(no transcript)"
    blocks, block = [], []
    for idx, w in enumerate(words):
        block.append(f"{w['word']}[{w['start']:.2f}-{w['end']:.2f}]")
        if len(block) >= 15 or idx == len(words) - 1:
            blocks.append(" ".join(block))
            block = []
            if len(blocks) >= max_blocks:
                break
    return "\n".join(blocks)


def _fallback_plan(duration: float) -> EditPlan:
    end_time = min(45.0, duration)
    return EditPlan(
        cuts=[Cut(start_time=0.0, end_time=end_time)],
        zooms=[
            Zoom(time=min(5.0, end_time), duration=2.0, scale=1.3, x=0.5, y=0.5),
            Zoom(time=min(15.0, end_time), duration=2.0, scale=1.4, x=0.5, y=0.5),
        ],
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_edit_plan(transcript: dict, video_title: str) -> EditPlan:
    """Auto-mode: derive the best Short from the transcript without a user prompt."""
    logger.info("Generating auto EditPlan for: %s", video_title)
    words = transcript.get("words", [])
    if not words:
        return _fallback_plan(30.0)

    duration = words[-1]["end"]
    transcript_text = _compact_transcript(transcript)

    system_prompt = (
        _SYSTEM_BASE
        + """
STYLE: Auto
1. Select the most engaging 30-60 second segment.
2. Add strategic zooms (scale 1.2-1.5) on key words/reactions (1-3 s each).
3. Speed up filler/silence (1.5-2x); slow down high-impact moments (0.75-0.85x).
4. Add minimal SFX: whoosh.mp3 on zooms, pop.mp3 for text reveals.
"""
    )
    user_prompt = (
        f"Video Title: {video_title}\n"
        f"Total Duration: {duration:.2f} seconds\n\n"
        f"Transcript (word[start-end]):\n{transcript_text}\n\n"
        "Generate the EditPlan now:"
    )

    try:
        plan = omniroute.completion(
            prompt=user_prompt,
            response_model=EditPlan,
            system_prompt=system_prompt,
            temperature=0.3,
        )
        logger.info("Auto EditPlan generated via OmniRoute.")
        return plan
    except Exception as exc:
        logger.error("Auto EditPlan failed: %s — using fallback.", exc)
        return _fallback_plan(duration)


def generate_edit_plan_from_prompt(
    user_prompt: str,
    transcript: dict,
    video_title: str,
) -> EditPlan:
    """
    Prompt-driven mode: the user supplies natural-language editing directions.

    The AI Editing Director interprets the creative intent and translates it
    into a precisely timed EditPlan that OpenClipCut executes — the AI never
    directly touches the video file.

    Example prompts:
      - "Make it very fast with quick cuts, zoom hard on every key word"
      - "Keep it chill — let one powerful moment breathe for 30 seconds"
      - "Find the funniest 45 seconds, add a pop SFX on the punchline"
    """
    logger.info(
        "Generating prompt-driven EditPlan for '%s' — prompt: %s",
        video_title, user_prompt[:120],
    )
    words = transcript.get("words", [])
    if not words:
        return _fallback_plan(30.0)

    duration = words[-1]["end"]
    transcript_text = _compact_transcript(transcript)

    system_prompt = (
        _SYSTEM_BASE
        + """
STYLE: User-directed
Follow the user's creative directions precisely while producing valid timestamps.
If instructions conflict with a hard constraint (e.g. >60 s Short), trim to 60 s.
"""
    )
    director_prompt = (
        f"Video Title: {video_title}\n"
        f"Total Duration: {duration:.2f} seconds\n\n"
        f"USER EDITING INSTRUCTIONS:\n{user_prompt}\n\n"
        f"Transcript (word[start-end]):\n{transcript_text}\n\n"
        "Apply the instructions and generate the EditPlan now:"
    )

    try:
        plan = omniroute.completion(
            prompt=director_prompt,
            response_model=EditPlan,
            system_prompt=system_prompt,
            temperature=0.35,
        )
        logger.info("Prompt-driven EditPlan generated via OmniRoute.")
        return plan
    except Exception as exc:
        logger.error("Prompt-driven EditPlan failed: %s — using fallback.", exc)
        return _fallback_plan(duration)
