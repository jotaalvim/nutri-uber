# -*- coding: utf-8 -*-
"""
Local food database nutrition lookup. Used by food cards and getnutritionvalues.
"""
import json
import re
from pathlib import Path

FOOD_DB_FILE = Path(__file__).parent / "food_database.json"
DEFAULT_SERVING_G = 200  # Typical restaurant portion

def _load_food_db() -> dict:
    """Load the full food database (foods + known_dishes)."""
    try:
        with open(FOOD_DB_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"foods": [], "known_dishes": []}


def load_local_db() -> list[dict]:
    """Load the local food database (per-100g foods)."""
    return _load_food_db().get("foods", [])


def load_known_dishes() -> list[dict]:
    """Load known dishes with exact per-serving nutrition."""
    return _load_food_db().get("known_dishes", [])


def _find_in_local_db(query: str, db: list[dict]) -> dict | None:
    """Search for a query match in the local database."""
    query_lower = query.lower()
    for food in db:
        if query_lower == food.get("name", "").lower():
            return food
    for food in db:
        for keyword in food.get("keywords", []):
            if keyword in query_lower:
                return food
    return None


def _extract_ingredients(name: str) -> list[str]:
    """Split menu item into potential individual ingredients."""
    cleaned = name.lower()
    cleaned = re.sub(r"^\d+\.?\s*", "", cleaned)
    cleaned = re.sub(r"\s*\(.*?\)", "", cleaned)
    stop_words = [
        "delicioso", "caseiro", "fresco", "tradicional", "da casa",
        "especial", "grelhado", "frito", "assado", "no forno", "molho", "com",
    ]
    for word in stop_words:
        cleaned = re.sub(r"\b" + word + r"\b", "", cleaned, flags=re.IGNORECASE)
    parts = re.split(r",|\s+(?:e|com|plus|\+|&)\s+", cleaned)
    return [p.strip() for p in parts if len(p.strip()) > 2]


def get_nutrition_per_100g(original_name: str, db: list[dict] | None = None) -> dict | None:
    """
    Get nutrition per 100g from local DB. Returns None if no match.
    Keys: energy_kcal_100g, proteins_100g, fat_100g, carbohydrates_100g, fiber_100g, product_name
    """
    if db is None:
        db = load_local_db()
    if not db:
        return None

    ingredients = _extract_ingredients(original_name)
    if not ingredients:
        ingredients = [original_name]

    results = []
    for ing in ingredients:
        food_data = _find_in_local_db(ing, db)
        if food_data:
            results.append(food_data)

    if not results:
        return None

    # If we extracted multiple ingredients but only matched low-calorie items (e.g. salad),
    # the main probably didn't match - skip to avoid wrong estimate
    if len(ingredients) > 1 and len(results) == 1:
        energy = results[0].get("energy_kcal_100g", 0) or 0
        if energy < 80:  # salad, veg only
            return None

    keys = ["energy_kcal_100g", "proteins_100g", "fat_100g", "carbohydrates_100g", "fiber_100g"]
    avg = {}
    for k in keys:
        vals = [r.get(k, 0) for r in results]
        avg[k] = round(sum(vals) / len(vals), 2) if vals else 0
    avg["product_name"] = " + ".join(r["name"] for r in results)
    return avg


def get_nutrition_for_serving(
    original_name: str,
    serving_g: float = DEFAULT_SERVING_G,
    db: list[dict] | None = None,
    restaurant: str | None = None,
) -> dict | None:
    """
    Get nutrition for a typical serving. Returns nutriments dict compatible with
    food cards: energy_kcal, protein, carbohydrate, fat, fiber.
    Uses known_dishes first, then per-100g foods from database. No LLM or external APIs.
    """
    search_text = " ".join(filter(None, [original_name, restaurant])).lower()
    for dish in load_known_dishes():
        keywords = dish.get("keywords", [])
        if not keywords:
            continue
        match_all = dish.get("match_all", False)
        if match_all:
            if not all(kw in search_text for kw in keywords):
                continue
        else:
            if not any(kw in search_text for kw in keywords):
                continue
        return {
            "product_name": dish.get("product_name", original_name),
            "nutriments": {
                "energy_kcal": dish.get("energy_kcal", 0),
                "protein": dish.get("protein", 0),
                "carbohydrate": dish.get("carbohydrate", 0),
                "fat": dish.get("fat", 0),
                "fiber": dish.get("fiber", 0),
            },
            "source": "local_db",
        }

    per_100 = get_nutrition_per_100g(original_name, db)
    if not per_100:
        return None

    factor = serving_g / 100.0
    return {
        "product_name": per_100.get("product_name", original_name),
        "nutriments": {
            "energy_kcal": round(per_100.get("energy_kcal_100g", 0) * factor, 0),
            "protein": round(per_100.get("proteins_100g", 0) * factor, 1),
            "carbohydrate": round(per_100.get("carbohydrates_100g", 0) * factor, 1),
            "fat": round(per_100.get("fat_100g", 0) * factor, 1),
            "fiber": round(per_100.get("fiber_100g", 0) * factor, 1),
        },
        "source": "local_db",
    }
