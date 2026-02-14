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

from cache import get as cache_get, set as cache_set
from food_finder import (
    find_food_for_patient,
    load_patient_diet,
)

app = Flask(__name__)
CORS(app)

DATA_DIR = Path(__file__).parent / "data"
DEFAULT_JSONL = DATA_DIR / "input_nutri_approval (3).jsonl"


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
        payload = {
            "patient": patient.get("patient_name", "Unknown"),
            "count": len(results),
            "items": results,
        }
        cache_set(patient_id, city, payload["patient"], results)
        return jsonify(payload)
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 404
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


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    print(f"Nutri-Uber API starting on http://127.0.0.1:{port}")
    print(f"Health check: curl http://127.0.0.1:{port}/health")
    app.run(host="0.0.0.0", port=port, debug=True)
