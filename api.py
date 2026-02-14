#!/usr/bin/env python3
"""
Nutri-Uber API - Simple Flask server for the food finder.
POST /find_food with JSON body: { "patient": {...}, "patient_id": 1, "city": "braga-norte" }
GET  /find_food?file=path/to/patients.jsonl&patient_index=0&city=braga-norte
GET  /warm_cache?patient_id=1&city=braga-norte - Pre-fetch in background (returns immediately)
"""

import json
import os
import re
import threading
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

from flask import Flask, jsonify, request
from flask_cors import CORS

from cache import get as cache_get, set as cache_set, get_grocery, set_grocery, get_nutrition, set_nutrition, list_grocery_baskets
from uber_eats_integration import add_basket_to_cart
from food_finder import (
    find_food_for_patient,
    find_grocery_basket_for_patient,
    fetch_uber_eats_images_for_items,
    load_all_menus_items,
    load_continente_grocery_from_all_menus,
    load_patient_diet,
    _is_drink,
)


def _filter_drinks(items: list) -> list:
    """Remove drink items from list."""
    return [
        i for i in items
        if i.get("basket_role") != "drink"
        and not _is_drink(i.get("name") or "", i.get("description") or "")
    ]
from calorie_estimator import estimate_calories_with_llm

app = Flask(__name__)
CORS(app)

DATA_DIR = Path(__file__).parent / "data"
DEFAULT_JSONL = DATA_DIR / "input_nutri_approval (3).jsonl"

# Uber Eats grocery/shop feed - valid link for grocery items
SHOP_FEED_URL = "https://www.ubereats.com/feeds/shop_feed"

# Pre-built baskets when Uber Eats scrape returns empty (e.g. in Docker, no address)
SEED_BASKETS = [
    {
        "store": "Uber Eats Grocery",
        "store_url": SHOP_FEED_URL,
        "items": [
            {"name": "Peito de frango grelhado", "price": "€6.90", "basket_role": "protein",
             "macronutrient_distribution_in_grams": {"protein": 31, "carbohydrate": 0, "fat": 3.6}},
            {"name": "Arroz integral", "price": "€2.50", "basket_role": "carbohydrate",
             "macronutrient_distribution_in_grams": {"protein": 2.6, "carbohydrate": 23, "fat": 1.9}},
            {"name": "Salada mista", "price": "€3.90", "basket_role": "vegetable",
             "macronutrient_distribution_in_grams": {"protein": 1.5, "carbohydrate": 4, "fat": 0.3}},
            {"name": "Maçã", "price": "€0.80", "basket_role": "vegetable_or_fruit",
             "macronutrient_distribution_in_grams": {"protein": 0.3, "carbohydrate": 14, "fat": 0.2}},
        ],
    },
    {
        "store": "Uber Eats Grocery",
        "store_url": SHOP_FEED_URL,
        "items": [
            {"name": "Salmão grelhado", "price": "€8.90", "basket_role": "protein",
             "macronutrient_distribution_in_grams": {"protein": 25, "carbohydrate": 0, "fat": 13}},
            {"name": "Arroz de sushi", "price": "€2.90", "basket_role": "carbohydrate",
             "macronutrient_distribution_in_grams": {"protein": 2.4, "carbohydrate": 28, "fat": 0.3}},
            {"name": "Edamame", "price": "€3.50", "basket_role": "vegetable",
             "macronutrient_distribution_in_grams": {"protein": 11, "carbohydrate": 10, "fat": 5}},
            {"name": "Abacate", "price": "€2.00", "basket_role": "vegetable_or_fruit",
             "macronutrient_distribution_in_grams": {"protein": 2, "carbohydrate": 9, "fat": 15}},
        ],
    },
]


def _seed_cache_on_startup():
    """Seed grocery cache so we always have items when scrape fails (Docker, no address)."""
    try:
        patients = load_patient_diet(str(DEFAULT_JSONL))
        city = "braga-norte"
        for i, patient in enumerate(patients[:8]):
            patient_id = i + 1
            if get_grocery(patient_id, city):
                continue  # Already cached
            template = SEED_BASKETS[i % len(SEED_BASKETS)]
            items = [
                {**it, "restaurant": template["store"], "restaurant_url": template["store_url"],
                 "store_url": template["store_url"]}
                for it in template["items"]
            ]
            fetch_uber_eats_images_for_items(items, headless=True)
            total_macros = {"protein": 0, "carbohydrate": 0, "fat": 0}
            for it in items:
                m = it.get("macronutrient_distribution_in_grams") or {}
                total_macros["protein"] += m.get("protein", 0) or 0
                total_macros["carbohydrate"] += m.get("carbohydrate", 0) or 0
                total_macros["fat"] += m.get("fat", 0) or 0
            set_grocery(patient_id, city, {
                "patient": patient.get("patient_name", f"Paciente {patient_id}"),
                "store": template["store"],
                "store_url": template["store_url"],
                "items": items,
                "total_macros": total_macros,
                "count": len(items),
            })
        # Seed for common IDs 6-8 in case DB has more patients
        for extra_id in [6, 7, 8]:
            if get_grocery(extra_id, city):
                continue
            template = SEED_BASKETS[extra_id % len(SEED_BASKETS)]
            items = [
                {**it, "restaurant": template["store"], "restaurant_url": template["store_url"],
                 "store_url": template["store_url"]}
                for it in template["items"]
            ]
            fetch_uber_eats_images_for_items(items, headless=True)
            total_macros = {"protein": 0, "carbohydrate": 0, "fat": 0}
            for it in items:
                m = it.get("macronutrient_distribution_in_grams") or {}
                total_macros["protein"] += m.get("protein", 0) or 0
                total_macros["carbohydrate"] += m.get("carbohydrate", 0) or 0
                total_macros["fat"] += m.get("fat", 0) or 0
            set_grocery(extra_id, city, {
                "patient": f"Paciente {extra_id}",
                "store": template["store"],
                "store_url": template["store_url"],
                "items": items,
                "total_macros": total_macros,
                "count": len(items),
            })
    except Exception:
        pass


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "nutri-uber"})


def _resolve_patient_and_params():
    """Resolve patient, patient_id, city, max_restaurants from request."""
    patient_id = None
    if request.method == "POST":
        data = request.get_json() or {}
        patient_id = data.get("patient_id")
        patient = data.get("patient")
        if not patient:
            filepath = data.get("file") or str(DEFAULT_JSONL)
            patient_index = int(data.get("patient_index", 0))
            patients = load_patient_diet(filepath)
            patient = patients[min(patient_index, len(patients) - 1)]
        city = data.get("city", "braga-norte")
        max_restaurants = min(int(data.get("max_restaurants", 3)), 5)
    else:
        filepath = request.args.get("file") or str(DEFAULT_JSONL)
        patient_index = int(request.args.get("patient_index", 0))
        patients = load_patient_diet(filepath)
        patient = patients[min(patient_index, len(patients) - 1)]
        city = request.args.get("city", "braga-norte")
        max_restaurants = min(int(request.args.get("max_restaurants", 3)), 5)
    return patient, patient_id, city, max_restaurants


@app.route("/find_food", methods=["GET", "POST"])
def find_food():
    """
    Find healthy food that fits the patient's dietary constraints.
    Cache hit = instant. Cache miss = scrape or fallback to all_menus.
    """
    try:
        patient, patient_id, city, max_restaurants = _resolve_patient_and_params()

        # Cache check - instant return if ready
        cached = cache_get(patient_id, city)
        if cached:
            return jsonify(cached)

        results = []
        try:
            results = find_food_for_patient(
                patient,
                city_slug=city,
                max_restaurants=max_restaurants,
                headless=True,
            )
        except Exception:
            pass  # Fall through to fallback

        if not results:
            # Fallback: instant items from all_menus.json (no scraping)
            results = load_all_menus_items(max_items=40)

        results = _filter_drinks(results)
        if results:
            fetch_uber_eats_images_for_items(results, headless=True)  # concurrent, non-blocking
            cache_set(patient_id, city, patient.get("patient_name", "Unknown"), results)

        payload = {
            "patient": patient.get("patient_name", "Unknown"),
            "count": len(results),
            "items": results,
        }
        return jsonify(payload)
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/cached_grocery_basket", methods=["GET"])
def cached_grocery_basket():
    """Return cached grocery basket if available. Fallback to all_menus when cold."""
    try:
        patient_id = request.args.get("patient_id")
        city = request.args.get("city", "braga-norte")
        if not patient_id:
            return jsonify({"error": "patient_id required"}), 400

        cached = get_grocery(patient_id, city)
        if cached:
            cached["items"] = _filter_drinks(cached.get("items") or [])
            cached["count"] = len(cached["items"])
            return jsonify(cached)

        # Fallback: items from all_menus.json (instant, no scraping)
        items = _filter_drinks(load_continente_grocery_from_all_menus(max_items=20))
        if items:
            store_url = items[0].get("store_url", "") if items else ""
            return jsonify({
                "store": "Continente Bom Dia Braga",
                "store_url": store_url,
                "items": items,
                "count": len(items),
                "total_macros": {},
                "from_cache": False,
            })

        # Last resort: seed basket
        template = SEED_BASKETS[0]
        return jsonify({
            "store": template["store"],
            "store_url": template["store_url"],
            "items": template["items"],
            "count": len(template["items"]),
            "total_macros": {},
            "from_cache": False,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/cached_food", methods=["GET"])
def cached_food():
    """
    Return cached food for a patient if available. Fallback to all_menus when cold.
    Used to show results immediately on page load.
    """
    try:
        patient_id = request.args.get("patient_id")
        city = request.args.get("city", "braga-norte")
        if not patient_id:
            return jsonify({"error": "patient_id required"}), 400

        cached = cache_get(patient_id, city)
        if cached:
            cached["items"] = _filter_drinks(cached.get("items") or [])
            cached["count"] = len(cached["items"])
            return jsonify(cached)

        # Fallback: items from all_menus.json (instant, no scraping)
        items = _filter_drinks(load_all_menus_items(max_items=40))
        return jsonify({
            "patient": "Unknown",
            "count": len(items),
            "items": items,
            "from_cache": False,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/warm_cache", methods=["GET", "POST"])
def warm_cache():
    """
    Pre-fetch food for a patient in background. Returns immediately (202).
    Call when dashboard loads so cache is ready when user clicks "Find food".
    POST: { "patient_id": 1, "patient": {...}, "city": "braga-norte" }
    GET:  ?patient_id=1&patient_index=0&city=braga-norte (uses JSONL file)
    """
    try:
        data = request.get_json() or {}
        patient_id = request.args.get("patient_id") or data.get("patient_id")
        city = request.args.get("city", "braga-norte") or data.get("city", "braga-norte")
        patient = data.get("patient")

        if not patient:
            filepath = request.args.get("file") or data.get("file") or str(DEFAULT_JSONL)
            patient_index = int(request.args.get("patient_index", data.get("patient_index", 0)))
            patients = load_patient_diet(filepath)
            patient = patients[min(patient_index, len(patients) - 1)]

        # Already cached?
        cached = cache_get(patient_id, city)
        grocery = get_grocery(patient_id, city) if patient_id else None

        def _warm_nutrition():
            from nutrition_local_db import get_nutrition_for_serving
            items = list((cached.get("items") or [])[:6]) + list((grocery.get("items") or [])[:4]) if cached or grocery else []
            for item in items[:10]:
                name = (item.get("name") or "").strip()
                desc = item.get("description") or ""
                img = item.get("image_url") or ""
                if not name or get_nutrition(name, desc, img):
                    continue
                try:
                    d = get_nutrition_for_serving(name, restaurant=item.get("restaurant"))
                    if d and d.get("nutriments"):
                        set_nutrition(name, d, desc, img)
                except Exception:
                    pass

        if cached or grocery:
            threading.Thread(target=_warm_nutrition, daemon=True).start()
            return jsonify({"status": "cached", "count": (cached or {}).get("count", 0) or (grocery or {}).get("count", 0)})

        def _warm():
            try:
                results = find_food_for_patient(
                    patient,
                    city_slug=city,
                    max_restaurants=3,
                    headless=True,
                )
                cache_set(patient_id, city, patient.get("patient_name", "Unknown"), results)
            except Exception:
                pass

        threading.Thread(target=_warm, daemon=True).start()
        return jsonify({"status": "warming", "message": "Cache will be ready in ~40s"}), 202
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/grocery_basket", methods=["GET", "POST"])
def grocery_basket():
    """
    Build a ready-to-order grocery basket: healthy meal from local grocery/supermarket.
    Respects allergies, intolerances, dislikes; favors favorites.
    POST: { "patient": {...}, "patient_id": 1, "city": "braga-norte" }
    GET:  ?patient_id=1&patient_index=0&city=braga-norte
    """
    try:
        patient, patient_id, city, _ = _resolve_patient_and_params()

        cached = get_grocery(patient_id, city)
        if cached:
            return jsonify(cached)

        result = {}
        try:
            result = find_grocery_basket_for_patient(
                patient,
                city_slug=city,
                max_stores=2,
                headless=True,
            )
        except Exception:
            pass  # Fall through to fallback

        if not result.get("items"):
            # Fallback: items from all_menus.json or seed basket
            items = _filter_drinks(load_continente_grocery_from_all_menus(max_items=20))
            if items:
                store_url = items[0].get("store_url", "") if items else ""
                result = {
                    "store": "Continente Bom Dia Braga",
                    "store_url": store_url,
                    "items": items,
                    "count": len(items),
                    "total_macros": {},
                }
            else:
                template = SEED_BASKETS[0]
                result = {
                    "store": template["store"],
                    "store_url": template["store_url"],
                    "items": template["items"],
                    "count": len(template["items"]),
                    "total_macros": {},
                }

        if result.get("items"):
            result["items"] = _filter_drinks(result["items"])
            result["count"] = len(result["items"])
            fetch_uber_eats_images_for_items(result["items"], headless=True)  # concurrent
            set_grocery(patient_id, city, result)
        return jsonify(result)
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/add_basket_to_cart", methods=["POST"])
def add_basket_to_cart_endpoint():
    """
    Add basket items to Uber Eats cart. Uses user's Chrome (already logged in).
    POST: { "store_url": "...", "items": [{ "name": "..." }, ...] }
    Requires CHROME_CDP_URL. Opens new tab, adds items.
    """
    try:
        data = request.get_json() or {}
        store_url = data.get("store_url")
        items = data.get("items", [])
        if not store_url or not items:
            return jsonify({"error": "store_url and items required"}), 400

        headless = data.get("headless", False)
        keep_open = data.get("keep_open", True)

        result = add_basket_to_cart(
            store_url=store_url,
            items=items,
            headless=headless,
            keep_open=keep_open,
        )
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/baskets", methods=["GET"])
def baskets():
    """
    List all cached grocery baskets available.
    GET /baskets
    """
    try:
        baskets_list = list_grocery_baskets()
        return jsonify({
            "count": len(baskets_list),
            "baskets": baskets_list,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/nutrition", methods=["GET", "POST"])
def nutrition():
    """
    Estimate calories using OpenAI Vision (image + description) or fallback to Open Food Facts.
    Cached for 7 days - cache hit returns instantly.
    GET  /nutrition?q=chicken+salad
    POST /nutrition with JSON: { "q": "food name", "description": "...", "image_url": "https://..." }
    """
    try:
        if request.method == "POST":
            data = request.get_json() or {}
            q = (data.get("q") or data.get("name") or "").strip()
            description = (data.get("description") or "").strip()
            image_url = (data.get("image_url") or "").strip() or None
        else:
            q = request.args.get("q", "").strip()
            description = request.args.get("description", "").strip()
            image_url = request.args.get("image_url", "").strip() or None

        if not q:
            return jsonify({"error": "q (food name) required"}), 400

        force_refresh = request.args.get("refresh") == "1" or (request.get_json() or {}).get("refresh") is True
        cached = None if force_refresh else get_nutrition(q, description, image_url or "")
        if cached:
            return jsonify(cached)

        from nutrition_local_db import get_nutrition_for_serving

        detail = None
        if force_refresh:
            detail = estimate_calories_with_llm(
                food_name=q,
                description=description,
                image_url=image_url,
            )
        if not detail or not detail.get("nutriments"):
            detail = get_nutrition_for_serving(q)
        if not detail or not detail.get("nutriments"):
            return jsonify({"product_name": q, "nutriments": {}, "source": "local_db"})

        set_nutrition(q, detail, description, image_url or "")
        return jsonify(detail)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/bowel_impact", methods=["POST"])
def bowel_impact():
    """
    Estimate how a food might affect bowel movement for the patient.
    POST /bowel_impact with JSON: { "patient_infos": {...}, "food_item": { "name": "...", "description": "..." } }
    Returns: { "message": str } - short message in Portuguese about bowel impact
    """
    try:
        data = request.get_json() or {}
        patient_infos = data.get("patient_infos") or {}
        food_item = data.get("food_item") or {}
        food_name = (food_item.get("name") or "").strip()
        food_desc = (food_item.get("description") or "").strip()

        if not food_name:
            return jsonify({"message": None})

        bowel = patient_infos.get("bowel_movements") or {}
        bowel_type = (bowel.get("type") or "").strip()
        bowel_details = (bowel.get("details") or "").strip()

        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not api_key:
            return jsonify({"message": None})

        try:
            from openai import OpenAI
            client = OpenAI(api_key=api_key)
        except ImportError:
            return jsonify({"message": None})

        prompt = f"""You are a clinical nutritionist. Give a brief, friendly message about how this food might affect bowel movement.

PATIENT BOWEL STATUS: {bowel_type or "Not specified"}. {bowel_details or ""}

FOOD: {food_name}
{f"Description: {food_desc}" if food_desc else ""}

Consider: fiber content (promotes regularity), fat (can slow digestion), spicy/irritating foods, dairy (lactose), caffeine, alcohol. Give 1-2 sentences in Portuguese. Be helpful and non-alarming. If the food is neutral, say something brief like: Este prato não deve ter impacto significativo no trânsito intestinal."""

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=150,
        )
        reply = (response.choices[0].message.content or "").strip()
        return jsonify({"message": reply if reply else None})
    except Exception as e:
        return jsonify({"message": None, "error": str(e)})


@app.route("/check_food_medication", methods=["POST"])
def check_food_medication():
    """
    Check if a food item may interact badly with the patient's medications.
    POST /check_food_medication with JSON: { "patient_infos": {...}, "food_item": { "name": "...", "description": "..." } }
    Returns: { "has_risk": bool, "warning_message": str | null }
    """
    try:
        data = request.get_json() or {}
        patient_infos = data.get("patient_infos") or {}
        food_item = data.get("food_item") or {}
        food_name = (food_item.get("name") or "").strip()
        food_desc = (food_item.get("description") or "").strip()

        if not food_name:
            return jsonify({"has_risk": False, "warning_message": None})

        medical = patient_infos.get("medical_history") or {}
        medications = (medical.get("medications") or "").strip()
        diseases = ""
        if isinstance(medical.get("diseases"), dict):
            diseases = (medical.get("diseases", {}).get("details") or "").strip()
        elif isinstance(medical.get("diseases"), str):
            diseases = medical.get("diseases", "").strip()

        if not medications or medications.lower() in ("não", "nenhum", "none", "n/a", ""):
            return jsonify({"has_risk": False, "warning_message": None})

        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not api_key:
            return jsonify({"has_risk": False, "warning_message": None})

        try:
            from openai import OpenAI
            client = OpenAI(api_key=api_key)
        except ImportError:
            return jsonify({"has_risk": False, "warning_message": None})

        prompt = f"""You are a clinical nutritionist. Check if this food may interact badly with the patient's medications.

PATIENT MEDICATIONS: {medications}
PATIENT CONDITIONS (if relevant): {diseases or "Not specified"}

FOOD: {food_name}
{f"Description: {food_desc}" if food_desc else ""}

Known drug-food interactions to consider:
- Grapefruit/grapefruit juice: interacts with statins, calcium channel blockers, some immunosuppressants
- High-potassium foods (bananas, oranges, potatoes, spinach, tomatoes): caution with ACE inhibitors, ARBs (e.g. losartan), potassium-sparing diuretics
- Tyramine-rich foods (aged cheese, cured meats, fermented foods): caution with MAOIs
- Vitamin K-rich foods (leafy greens): affects warfarin
- Alcohol: interacts with many medications (anticonvulsants, metformin, etc.)
- Lamotrigine (Lamitor): avoid alcohol; grapefruit may affect levels
- Caffeine: may interact with some stimulants or sedatives

Reply with ONLY a JSON object, no other text:
- If there is a meaningful risk: {{"has_risk": true, "warning_message": "Clear 1-2 sentence explanation in Portuguese for the patient"}}
- If no significant risk: {{"has_risk": false, "warning_message": null}}

Be conservative: only flag real, well-documented interactions. Do not warn for trivial or speculative risks."""

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=256,
        )
        reply = (response.choices[0].message.content or "").strip()
        try:
            start = reply.find("{")
            if start >= 0:
                depth = 0
                end = -1
                for i, c in enumerate(reply[start:], start):
                    if c == "{":
                        depth += 1
                    elif c == "}":
                        depth -= 1
                        if depth == 0:
                            end = i
                            break
                if end >= 0:
                    parsed = json.loads(reply[start : end + 1])
                    return jsonify({
                        "has_risk": bool(parsed.get("has_risk")),
                        "warning_message": parsed.get("warning_message") or None,
                    })
        except (json.JSONDecodeError, TypeError):
            pass
        return jsonify({"has_risk": False, "warning_message": None})
    except Exception as e:
        return jsonify({"has_risk": False, "warning_message": None, "error": str(e)})


@app.route("/chat", methods=["POST"])
def chat():
    """
    Chat with the nutrition assistant LLM.
    POST /chat with JSON: { "messages": [...], "food_items": [...], "confirm_second_order": bool, "pending_item": {...} }
    When confirm_second_order is true, the assistant decides if the user should order again today. Returns: { "message": "...", "order_approved": bool }
    """
    try:
        data = request.get_json() or {}
        messages = data.get("messages") or []
        if not isinstance(messages, list):
            return jsonify({"error": "messages must be an array"}), 400

        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not api_key:
            return jsonify({"error": "OPENAI_API_KEY not configured"}), 503

        try:
            from openai import OpenAI
            client = OpenAI(api_key=api_key)
        except ImportError:
            return jsonify({"error": "openai package not installed"}), 503

        confirm_second_order = data.get("confirm_second_order") is True
        pending_item = data.get("pending_item") or {}

        if confirm_second_order and pending_item:
            item_name = pending_item.get("name", "this item")
            item_restaurant = pending_item.get("restaurant", "")
            system_prompt = f"""You are a supportive nutrition assistant. The user has ALREADY ordered in today and is trying to order again: "{item_name}" from {item_restaurant}.

Your job: Have a brief, empathetic conversation to understand if they really need to order again. Consider: genuine hunger, social plans, lack of groceries, etc. vs. impulse or habit.

After the user explains, decide:
- If they have a good reason (e.g. didn't have time to cook, unexpected guests, genuinely hungry): approve the order. Reply with your supportive message, then on a NEW LINE write exactly: ORDER_APPROVED
- If it seems unnecessary (e.g. boredom, impulse): gently suggest they skip. Reply with your message, then on a NEW LINE write exactly: ORDER_DENIED

Keep your message concise (1-3 sentences). Always end with ORDER_APPROVED or ORDER_DENIED on its own line."""
            formatted = [{"role": "system", "content": system_prompt}]
            for m in messages[-10:]:
                role = (m.get("role") or "user").lower()
                if role not in ("user", "assistant", "system"):
                    role = "user"
                content = (m.get("content") or "").strip()
                if content:
                    formatted.append({"role": role, "content": content})

            if not any(m.get("role") == "user" for m in formatted[1:]):
                return jsonify({"error": "At least one user message required"}), 400

            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=formatted,
                max_tokens=256,
            )
            reply = (response.choices[0].message.content or "").strip()
            order_approved = "ORDER_APPROVED" in reply.upper() and "ORDER_DENIED" not in reply.upper()
            message = reply.replace("ORDER_APPROVED", "").replace("ORDER_DENIED", "").strip()
            return jsonify({"message": message, "order_approved": order_approved})

        food_items = data.get("food_items") or []
        patient_infos = data.get("patient_infos") or {}
        medical = patient_infos.get("medical_history") or {}
        medications = (medical.get("medications") or "").strip()
        diseases = ""
        if isinstance(medical.get("diseases"), dict):
            diseases = (medical.get("diseases", {}).get("details") or "").strip()
        elif isinstance(medical.get("diseases"), str):
            diseases = medical.get("diseases", "").strip()
        med_context = ""
        if medications and medications.lower() not in ("não", "nenhum", "none", "n/a", ""):
            med_context = f"""
IMPORTANT - Patient's medications: {medications}
{f"Patient conditions: {diseases}" if diseases else ""}
When recommending food, AVOID or WARN about items that may interact badly with these medications. Common interactions: grapefruit with statins/calcium channel blockers; high-potassium foods (bananas, oranges, spinach) with losartan/ACE inhibitors; alcohol with anticonvulsants; tyramine-rich foods with MAOIs. Prefer safer alternatives when possible."""

        if isinstance(food_items, list) and food_items:
            def _fmt(it):
                m = it.get("macronutrient_distribution_in_grams") or {}
                role = (" | " + (it.get("basket_role") or "")) if it.get("basket_role") else ""
                return f"- {it.get('name', '?')} @ {it.get('restaurant', '?')} | {it.get('price', '')} | P:{m.get('protein', '?')}g C:{m.get('carbohydrate', '?')}g F:{m.get('fat', '?')}g{role}"
            items_text = "\n".join(_fmt(it) for it in food_items[:30])
            system_prompt = f"""You are a friendly nutrition assistant for the Nutri Dashboard app. The user sees food cards they can order. Your job is to RECOMMEND specific items from the list below when they ask what to eat, what to order, or for suggestions.
{med_context}

AVAILABLE FOOD ITEMS (recommend ONLY from this list):
{items_text}

When the user asks for recommendations, picks, suggestions, or "what should I order/eat", suggest 1–3 specific items from the list above by name and restaurant. Explain briefly why they fit (e.g. macros, role). Avoid or warn about items that may interact with the patient's medications. If they ask about nutrition in general, answer that too. Keep answers concise. Use metric units (grams, kcal)."""
        else:
            system_prompt = f"""You are a friendly nutrition assistant for the Nutri Dashboard app. Help users with meal planning, macros, and healthy eating. No food cards are loaded yet—suggest they click "Find food" first to see orderable items, or answer general nutrition questions.{med_context}
Keep answers concise. Use metric units (grams, kcal)."""

        formatted = [{"role": "system", "content": system_prompt}]
        for m in messages[-20:]:  # last 20 messages for context
            role = (m.get("role") or "user").lower()
            if role not in ("user", "assistant", "system"):
                role = "user"
            content = (m.get("content") or "").strip()
            if content:
                formatted.append({"role": role, "content": content})

        if not any(m.get("role") == "user" for m in formatted[1:]):
            return jsonify({"error": "At least one user message required"}), 400

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=formatted,
            max_tokens=1024,
        )
        reply = (response.choices[0].message.content or "").strip()
        return jsonify({"message": reply})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/patients", methods=["GET"])
def list_patients():
    """List available patients from the default JSONL file."""
    try:
        filepath = request.args.get("file") or str(DEFAULT_JSONL)
        patients = load_patient_diet(filepath)
        return jsonify({
            "count": len(patients),
            "patients": [p.get("patient_name", f"Patient {i}") for i, p in enumerate(patients)],
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# Seed cache in background when app loads (gunicorn or python api.py)
threading.Thread(target=_seed_cache_on_startup, daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    print(f"Nutri-Uber API starting on http://127.0.0.1:{port}")
    print(f"Health check: curl http://127.0.0.1:{port}/health")
    app.run(host="0.0.0.0", port=port, debug=True, threaded=True)
