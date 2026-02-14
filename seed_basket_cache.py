#!/usr/bin/env python3
"""
Seed the API cache with pre-built healthy ready-to-go baskets.
Run: python seed_basket_cache.py
"""

import json
from pathlib import Path

# Import after ensuring we're in the right directory
import sys
sys.path.insert(0, str(Path(__file__).parent))

from cache import set_grocery
from food_finder import load_patient_diet

# Healthy basket templates - balanced meals with protein, carbs, veg
BASKETS = [
    {
        "store": "Continente Take-Away",
        "store_url": "https://www.ubereats.com/pt-en/brand-city/braga-norte/continente-take-away",
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
        "store": "Poke House",
        "store_url": "https://www.ubereats.com/pt-en/store/poke-house-braga/45msceyNWyOEMtXPV8slVg",
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
    {
        "store": "MÉZE BRUNCH",
        "store_url": "https://www.ubereats.com/pt-en/store/meze-brunch/l0a4eW4sRrqt1whHG6XEOg",
        "items": [
            {"name": "Ovos mexidos com espinafre", "price": "€7.80", "basket_role": "protein",
             "macronutrient_distribution_in_grams": {"protein": 14, "carbohydrate": 2, "fat": 12}},
            {"name": "Pão integral torrado", "price": "€2.50", "basket_role": "carbohydrate",
             "macronutrient_distribution_in_grams": {"protein": 4, "carbohydrate": 24, "fat": 1}},
            {"name": "Tomate e pepino", "price": "€3.00", "basket_role": "vegetable",
             "macronutrient_distribution_in_grams": {"protein": 1, "carbohydrate": 4, "fat": 0.2}},
            {"name": "Banana", "price": "€1.00", "basket_role": "vegetable_or_fruit",
             "macronutrient_distribution_in_grams": {"protein": 1.3, "carbohydrate": 23, "fat": 0.4}},
            {"name": "Sumo laranja natural", "price": "€3.50", "basket_role": "drink",
             "macronutrient_distribution_in_grams": {"protein": 1.7, "carbohydrate": 26, "fat": 0.5}},
        ],
    },
]


def _enrich_item(item: dict, store: str, store_url: str, patient_name: str) -> dict:
    """Add restaurant, restaurant_url, description to item."""
    return {
        **item,
        "restaurant": store,
        "restaurant_url": store_url,
        "description": item.get("description"),
    }


def main():
    data_path = Path(__file__).parent / "data" / "input_nutri_approval (3).jsonl"
    patients = load_patient_diet(data_path)
    city = "braga-norte"

    for i, patient in enumerate(patients):
        patient_id = i + 1  # Rails DB IDs start at 1
        patient_name = patient.get("patient_name", f"Paciente {patient_id}")

        # Rotate through basket templates
        basket_template = BASKETS[i % len(BASKETS)]
        store = basket_template["store"]
        store_url = basket_template["store_url"]
        raw_items = basket_template["items"]

        items = [
            _enrich_item(it, store, store_url, patient_name)
            for it in raw_items
        ]

        total_macros = {"protein": 0, "carbohydrate": 0, "fat": 0}
        for it in items:
            m = it.get("macronutrient_distribution_in_grams") or {}
            total_macros["protein"] += m.get("protein", 0) or 0
            total_macros["carbohydrate"] += m.get("carbohydrate", 0) or 0
            total_macros["fat"] += m.get("fat", 0) or 0

        data = {
            "patient": patient_name,
            "store": store,
            "store_url": store_url,
            "items": items,
            "total_macros": total_macros,
            "count": len(items),
        }
        set_grocery(patient_id, city, data)
        print(f"Cached basket for {patient_name} (id={patient_id}): {len(items)} items @ {store}")

    # Also cache for a few extra patient IDs in case DB has more
    for extra_id in [6, 7, 8]:
        basket_template = BASKETS[extra_id % len(BASKETS)]
        store = basket_template["store"]
        store_url = basket_template["store_url"]
        items = [
            _enrich_item(it, store, store_url, f"Paciente {extra_id}")
            for it in basket_template["items"]
        ]
        total_macros = {"protein": 0, "carbohydrate": 0, "fat": 0}
        for it in items:
            m = it.get("macronutrient_distribution_in_grams") or {}
            total_macros["protein"] += m.get("protein", 0) or 0
            total_macros["carbohydrate"] += m.get("carbohydrate", 0) or 0
            total_macros["fat"] += m.get("fat", 0) or 0
        data = {
            "patient": f"Paciente {extra_id}",
            "store": store,
            "store_url": store_url,
            "items": items,
            "total_macros": total_macros,
            "count": len(items),
        }
        set_grocery(extra_id, city, data)
        print(f"Cached basket for Paciente {extra_id}: {len(items)} items @ {store}")

    print("Done. Grocery basket cache seeded.")


if __name__ == "__main__":
    main()
