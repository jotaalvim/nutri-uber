#!/usr/bin/env python3
"""
Calorie estimation using OpenAI Vision API.
Sends food image + description to the LLM and asks for calorie/macro estimates
based on Open Food Facts and typical nutritional knowledge.
"""

import base64
import json
import re
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")


def _fetch_image_as_base64(url: str) -> str | None:
    """Fetch image from URL and return base64 data URL for OpenAI."""
    if not url or not url.startswith("http"):
        return None
    try:
        import requests

        r = requests.get(url, timeout=8)
        if r.status_code != 200:
            return None
        ct = r.headers.get("content-type", "")
        if "image" not in ct and not ct.startswith("image/"):
            return None
        b64 = base64.standard_b64encode(r.content).decode("ascii")
        return f"data:{ct};base64,{b64}"
    except Exception:
        return None


def estimate_calories_with_llm(
    food_name: str,
    description: str = "",
    image_url: str | None = None,
) -> dict[str, Any] | None:
    """
    Use OpenAI Vision to estimate calories and macros from image + description.
    References Open Food Facts typical values. Returns nutriments dict or None.
    """
    api_key = __import__("os").environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        return None

    try:
        from openai import OpenAI

        client = OpenAI(api_key=api_key)
    except ImportError:
        return None

    content: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": f"""You are a nutrition expert. Estimate the calories and macronutrients for this food.

Food name: {food_name}
Description: {description or "(none)"}

Use your knowledge of typical values from Open Food Facts and common food databases.
Consider portion size visible in the image if provided.
Respond with a JSON object only, no other text:
{{
  "energy_kcal": <number, estimated kcal>,
  "protein": <number, grams>,
  "carbohydrate": <number, grams>,
  "fat": <number, grams>,
  "fiber": <number or null, grams>,
  "sugar": <number or null, grams>,
  "sodium": <number or null, grams>,
  "salt": <number or null, grams>,
  "confidence": "<low|medium|high>",
  "notes": "<brief explanation>"
}}""",
        }
    ]

    if image_url:
        if image_url.startswith("data:"):
            content.insert(0, {"type": "image_url", "image_url": {"url": image_url}})
        elif image_url.startswith("http"):
            img_b64 = _fetch_image_as_base64(image_url)
            if img_b64:
                content.insert(0, {"type": "image_url", "image_url": {"url": img_b64}})
            else:
                content.insert(0, {"type": "image_url", "image_url": {"url": image_url}})

    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": content}],
            max_tokens=500,
        )
        text = (response.choices[0].message.content or "").strip()
        if not text:
            return None

        json_match = re.search(r"\{[\s\S]*\}", text)
        if not json_match:
            return None
        data = json.loads(json_match.group(0))

        nutriments: dict[str, Any] = {}
        for key, off_key in [
            ("energy_kcal", "energy_kcal"),
            ("protein", "protein"),
            ("carbohydrate", "carbohydrate"),
            ("fat", "fat"),
            ("fiber", "fiber"),
            ("sugar", "sugar"),
            ("sodium", "sodium"),
            ("salt", "salt"),
        ]:
            val = data.get(off_key)
            if val is not None and isinstance(val, (int, float)):
                nutriments[key] = round(float(val), 1)

        return {
            "product_name": food_name,
            "nutriments": nutriments,
            "source": "openai_vision",
            "confidence": data.get("confidence"),
            "notes": data.get("notes"),
        }
    except Exception:
        return None
