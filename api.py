#!/usr/bin/env python3
"""
Nutri-Uber API - Simple Flask server for the food finder.
POST /find_food with JSON body: { "patient": {...}, "patient_id": 1, "city": "braga-norte" }
GET  /find_food?file=path/to/patients.jsonl&patient_index=0&city=braga-norte
GET  /warm_cache?patient_id=1&city=braga-norte - Pre-fetch in background (returns immediately)
"""

import os
import threading
from pathlib import Path

from flask import Flask, jsonify, request
from flask_cors import CORS

from cache import get as cache_get, set as cache_set, get_grocery, set_grocery, list_grocery_baskets
from uber_eats_integration import add_basket_to_cart
from food_finder import (
    find_food_for_patient,
    find_grocery_basket_for_patient,
    fetch_nutrition_detail,
    fetch_uber_eats_images_for_items,
    load_patient_diet,
)

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
            {"name": "Água 500ml", "price": "€1.20", "basket_role": "drink",
             "macronutrient_distribution_in_grams": {"protein": 0, "carbohydrate": 0, "fat": 0}},
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
            {"name": "Chá verde", "price": "€2.50", "basket_role": "drink",
             "macronutrient_distribution_in_grams": {"protein": 0, "carbohydrate": 0, "fat": 0}},
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
    Cache hit = instant. Cache miss = ~40s scrape.
    """
    try:
        patient, patient_id, city, max_restaurants = _resolve_patient_and_params()

        # Cache check - instant return if ready
        cached = cache_get(patient_id, city)
        if cached:
            return jsonify(cached)

        results = find_food_for_patient(
            patient,
            city_slug=city,
            max_restaurants=max_restaurants,
            headless=True,
        )
        fetch_uber_eats_images_for_items(results, headless=True)  # concurrent, non-blocking
        payload = {
            "patient": patient.get("patient_name", "Unknown"),
            "count": len(results),
            "items": results,
        }
        if results:
            cache_set(patient_id, city, payload["patient"], results)
        return jsonify(payload)
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/cached_grocery_basket", methods=["GET"])
def cached_grocery_basket():
    """Return cached grocery basket if available. No scraping."""
    try:
        patient_id = request.args.get("patient_id")
        city = request.args.get("city", "braga-norte")
        if not patient_id:
            return jsonify({"error": "patient_id required"}), 400

        cached = get_grocery(patient_id, city)
        if not cached:
            return jsonify({"error": "not cached"}), 404

        return jsonify(cached)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/cached_food", methods=["GET"])
def cached_food():
    """
    Return cached food for a patient if available. No scraping.
    Used to show results immediately on page load when cache is warm.
    """
    try:
        patient_id = request.args.get("patient_id")
        city = request.args.get("city", "braga-norte")
        if not patient_id:
            return jsonify({"error": "patient_id required"}), 400

        cached = cache_get(patient_id, city)
        if not cached:
            return jsonify({"error": "not cached"}), 404

        return jsonify(cached)
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
        if cached:
            return jsonify({"status": "cached", "count": cached["count"]})

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

        result = find_grocery_basket_for_patient(
            patient,
            city_slug=city,
            max_stores=2,
            headless=True,
        )
        if result.get("items"):
            fetch_uber_eats_images_for_items(result["items"], headless=True)  # concurrent
        # Fallback: when scrape returns empty, use seeded basket
        if not result.get("items"):
            cached = get_grocery(patient_id, city)
            if cached and cached.get("items"):
                result = cached
        if result.get("items"):
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


@app.route("/nutrition", methods=["GET"])
def nutrition():
    """
    Get detailed nutrition from Open Food Facts for a food name.
    GET /nutrition?q=chicken+salad
    """
    try:
        q = request.args.get("q", "").strip()
        if not q:
            return jsonify({"error": "q (query) required"}), 400
        detail = fetch_nutrition_detail(q)
        if not detail:
            return jsonify({"product_name": None, "nutriments": {}, "source": "openfoodfacts"})
        return jsonify(detail)
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
