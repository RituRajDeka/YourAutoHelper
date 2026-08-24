"""
OmniRoute — Tiered LLM routing gateway.

Priority order:
  1. OmniRoute local server  (http://localhost:20128/v1)  — OpenAI-compatible
  2. OmniRoute cloud mirror  (http://cloud.omniroute.online/v1) — OMNIROUTE_CLOUD_KEY
  3. Groq API                — GROQ_API_KEY
  4. Local Ollama            — fallback

Any tier that fails is transparently skipped and the next tier is tried.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any, Optional, Type, TypeVar

import requests
from pydantic import BaseModel

logger = logging.getLogger("ai_video_clipper.omniroute")

T = TypeVar("T", bound=BaseModel)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _extract_json(text: str) -> str:
    """Pull the first complete JSON object out of text."""
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        return fenced.group(1)
    start = text.find("{")
    if start != -1:
        depth = 0
        for i, ch in enumerate(text[start:], start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return text


def _build_messages(prompt: str, system_prompt: Optional[str]) -> list:
    msgs: list = []
    if system_prompt:
        msgs.append({"role": "system", "content": system_prompt})
    msgs.append({"role": "user", "content": prompt})
    return msgs


def _parse_response(text: str, response_model: Optional[Type[T]]) -> Any:
    if response_model is None:
        return text
    cleaned = _extract_json(text)
    return response_model.model_validate_json(cleaned)


# ---------------------------------------------------------------------------
# Tier 1 — OmniRoute local server
# ---------------------------------------------------------------------------

def _try_omniroute_server(prompt, response_model, system_prompt, temperature):
    """Call OmniRoute gateway at localhost:20128 (OpenAI-compatible)."""
    base_url = os.environ.get("OMNIROUTE_BASE_URL", "http://localhost:20128/v1")
    api_key = os.environ.get("OMNIROUTE_API_KEY", "omniroute")
    model = os.environ.get("OMNIROUTE_MODEL", "auto")
    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    body: dict[str, Any] = {
        "model": model,
        "messages": _build_messages(prompt, system_prompt),
        "temperature": temperature,
        "stream": False,
    }
    if response_model:
        body["response_format"] = {"type": "json_object"}
    logger.info("OmniRoute[server]: POST %s  model=%s", url, model)
    resp = requests.post(url, json=body, headers=headers, timeout=60)
    resp.raise_for_status()
    text = resp.json()["choices"][0]["message"]["content"]
    return _parse_response(text, response_model)


# ---------------------------------------------------------------------------
# Tier 2 — OmniRoute cloud mirror
# ---------------------------------------------------------------------------

def _try_omniroute_cloud(prompt, response_model, system_prompt, temperature):
    """Call OmniRoute cloud endpoint. Requires OMNIROUTE_CLOUD_KEY."""
    cloud_key = os.environ.get("OMNIROUTE_CLOUD_KEY", "")
    if not cloud_key:
        raise RuntimeError("OMNIROUTE_CLOUD_KEY not set — skipping cloud tier")
    model = os.environ.get("OMNIROUTE_CLOUD_MODEL", "auto")
    url = "http://cloud.omniroute.online/v1/chat/completions"
    headers = {"Authorization": f"Bearer {cloud_key}", "Content-Type": "application/json"}
    body: dict[str, Any] = {
        "model": model,
        "messages": _build_messages(prompt, system_prompt),
        "temperature": temperature,
        "stream": False,
    }
    if response_model:
        body["response_format"] = {"type": "json_object"}
    logger.info("OmniRoute[cloud]: POST %s  model=%s", url, model)
    resp = requests.post(url, json=body, headers=headers, timeout=90)
    resp.raise_for_status()
    text = resp.json()["choices"][0]["message"]["content"]
    return _parse_response(text, response_model)


# ---------------------------------------------------------------------------
# Tier 3 — Groq
# ---------------------------------------------------------------------------

def _try_groq(prompt, response_model, system_prompt, temperature):
    groq_key = os.environ.get("GROQ_API_KEY", "")
    if not groq_key:
        raise RuntimeError("GROQ_API_KEY not set — skipping Groq tier")
    try:
        from groq import Groq
    except ImportError:
        raise RuntimeError("groq package not installed")
    client = Groq(api_key=groq_key)
    # Guard against 413: Groq models have tight context windows.
    # Truncate prompts that exceed ~6000 chars (roughly 1500 tokens).
    MAX_PROMPT_CHARS = 6000
    if len(prompt) > MAX_PROMPT_CHARS:
        prompt = prompt[:MAX_PROMPT_CHARS] + "\n...[transcript truncated for model context limit]"
    messages = _build_messages(prompt, system_prompt)
    opts: dict[str, Any] = {}
    if response_model:
        opts["response_format"] = {"type": "json_object"}
    for model in ("compound-beta", "openai/gpt-oss-20b", "qwen/qwen3.6-27b"):
        try:
            logger.info("OmniRoute[groq]: model=%s", model)
            cc = client.chat.completions.create(
                messages=messages, model=model, temperature=temperature, **opts
            )
            return _parse_response(cc.choices[0].message.content, response_model)
        except Exception as exc:
            logger.warning("OmniRoute[groq]: %s failed — %s", model, exc)
    raise RuntimeError("All Groq models failed")


# ---------------------------------------------------------------------------
# Tier 4 — Local Ollama
# ---------------------------------------------------------------------------

def _try_ollama(prompt, response_model, system_prompt, temperature):
    url = "http://localhost:11434/api/chat"
    payload: dict[str, Any] = {
        "model": "llama3",
        "messages": _build_messages(prompt, system_prompt),
        "stream": False,
        "options": {"temperature": temperature},
    }
    if response_model:
        payload["format"] = "json"
    logger.info("OmniRoute[ollama]: POST %s", url)
    resp = requests.post(url, json=payload, timeout=120)
    resp.raise_for_status()
    text = resp.json()["message"]["content"]
    return _parse_response(text, response_model)



# ---------------------------------------------------------------------------
# Tier 5 - OpenAI
# ---------------------------------------------------------------------------
def _try_openai(prompt, response_model, system_prompt, temperature):
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set - skipping OpenAI tier")
    url = "https://api.openai.com/v1/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    body = {
        "model": "gpt-4o-mini",
        "messages": _build_messages(prompt, system_prompt),
        "temperature": temperature
    }
    if response_model:
        body["response_format"] = {"type": "json_object"}
        
    logger.info("OmniRoute[openai]: POST %s", url)
    resp = requests.post(url, json=body, headers=headers, timeout=30)
    resp.raise_for_status()
    text = resp.json()["choices"][0]["message"]["content"]
    return _parse_response(text, response_model)


# ---------------------------------------------------------------------------
# Tier 6 - Google Gemini
# ---------------------------------------------------------------------------
def _try_gemini(prompt, response_model, system_prompt, temperature):
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not set - skipping Gemini tier")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key}"
    headers = {"Content-Type": "application/json"}
    
    full_prompt = ""
    if system_prompt:
        full_prompt += f"{system_prompt}\n\n"
    full_prompt += prompt
    
    body = {
        "contents": [{
            "parts": [{"text": full_prompt}]
        }],
        "generationConfig": {
            "temperature": temperature
        }
    }
    if response_model:
        body["generationConfig"]["responseMimeType"] = "application/json"
        
    logger.info("OmniRoute[gemini]: POST %s", "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent")
    resp = requests.post(url, json=body, headers=headers, timeout=30)
    resp.raise_for_status()
    text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
    return _parse_response(text, response_model)


# ---------------------------------------------------------------------------
# Tier 7 - Rule-Based Fallback Generator
# ---------------------------------------------------------------------------
def _try_rule_based(prompt, response_model, system_prompt, temperature):
    logger.info("OmniRoute[rule_based]: Falling back to local rules...")
    if response_model is None:
        return "{}"
        
    class_name = response_model.__name__
    if class_name == "EditPlan":
        import re
        times = [float(x) for x in re.findall(r'\[(\d+\.\d+)-\d+\.\d+\]', prompt)]
        end_times = [float(x) for x in re.findall(r'\[\d+\.\d+-(\d+\.\d+)\]', prompt)]
        
        duration = 30.0
        if end_times:
            duration = max(end_times)
            
        start_cut = 0.0
        if duration > 45.0:
            start_cut = min(10.0, duration - 45.0)
        end_cut = min(start_cut + 45.0, duration)
        
        cuts = [{"start_time": start_cut, "end_time": end_cut}]
        zooms = [
            {"time": start_cut + 5.0, "duration": 2.0, "scale": 1.3, "x": 0.5, "y": 0.5},
            {"time": start_cut + 15.0, "duration": 2.0, "scale": 1.4, "x": 0.5, "y": 0.5}
        ]
        sfx = []
        prompt_lower = prompt.lower()
        if "beast" in prompt_lower or "ksi" in prompt_lower:
            sfx.append({"time": start_cut + 5.0, "name": "whoosh.mp3", "volume": 0.5})
            sfx.append({"time": start_cut + 15.0, "name": "pop.mp3", "volume": 0.5})
            
        data = {
            "cuts": cuts,
            "zooms": zooms,
            "speed_changes": [],
            "transitions": [],
            "sound_effects": sfx,
            "music_changes": [],
            "emphasis_points": [],
            "caption_preferences": None
        }
        return response_model.model_validate(data)
        
    elif class_name == "SEOMetadata":
        import re
        title_match = re.search(r'Original Video Title:\s*(.*)', prompt)
        orig_title = title_match.group(1).strip() if title_match else "Short"
        
        clean_title = orig_title[:50] + " ?? #shorts"
        data = {
            "title": clean_title,
            "description": f"Must watch highlight from {orig_title}! #shorts #viral #trending",
            "tags": ["shorts", "viral", "trending", "highlight"]
        }
        return response_model.model_validate(data)
        
    return response_model()

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def completion(
    prompt: str,
    response_model: Optional[Type[T]] = None,
    system_prompt: Optional[str] = None,
    temperature: float = 0.2,
) -> Any:
    """
    Route a prompt through the best available LLM provider.

    Providers tried in order:
      1. OmniRoute local  (localhost:20128)
      2. OmniRoute cloud  (requires OMNIROUTE_CLOUD_KEY)
      3. Groq             (requires GROQ_API_KEY)
      4. Ollama           (last resort)
    """
    tiers = [
        ("OmniRoute server", _try_omniroute_server),
        ("OmniRoute cloud", _try_omniroute_cloud),
        ("Groq", _try_groq),
        ("Ollama", _try_ollama),
        ("OpenAI", _try_openai),
        ("Gemini", _try_gemini),
        ("Rule-Based Fallback", _try_rule_based)
    ]
    last_exc: Optional[Exception] = None
    for name, fn in tiers:
        try:
            result = fn(prompt, response_model, system_prompt, temperature)
            logger.info("OmniRoute: success via %s", name)
            return result
        except Exception as exc:
            logger.warning("OmniRoute: %s unavailable — %s", name, exc)
            last_exc = exc
    raise RuntimeError(f"OmniRoute: all tiers exhausted. Last error: {last_exc}")
