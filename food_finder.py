#!/usr/bin/env python3
"""
Nutri-Uber Food Finder - Hackathon Edition
Given user dietary info, scrapes healthy food from Uber Eats that fits the user's constraints.
Uses Playwright for reliable browser automation (Uber Eats is JS-heavy).
"""

import json
import re
import time
import urllib.parse
from pathlib import Path
from typing import Any

import requests
from playwright.sync_api import sync_playwright

# Uber Eats URL structure
UBER_PREFIX = "https://www.ubereats.com"
UBER_PT = "https://www.ubereats.com/pt-en"


def load_patient_diet(filepath: str | Path) -> list[dict[str, Any]]:
    """Load patient dietary data from JSONL file."""
    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(f"Patient data file not found: {filepath}")

    patients = []
    with open(path, encoding="utf-8") as f:
        content = f.read().strip()
        # Handle both JSON array and JSONL (one JSON per line)
        if content.startswith("["):
            patients = json.loads(content)
        else:
            for line in content.split("\n"):
                if line.strip():
                    patients.append(json.loads(line))
    return patients


def extract_dietary_constraints(patient: dict[str, Any]) -> dict[str, Any]:
    """Extract dietary constraints from patient data for filtering."""
    dietary = patient.get("patient_infos", {}).get("dietary_history", {}) or {}
    medical = patient.get("patient_infos", {}).get("medical_history", {}) or {}

    def safe_list(val: Any) -> list[str]:
        if isinstance(val, dict):
            lst = val.get("list", []) or []
            details = (val.get("details") or "").strip()
            if details and details.lower() not in ("não tem", "nenhum", "none", "—", "n/a", "nenhuma"):
                # Split comma-separated values
                for part in re.split(r"[,;]", details):
                    part = part.strip()
                    if part:
                        lst.append(part)
            return [p.strip().lower() for p in lst if p]
        if isinstance(val, str):
            return [p.strip().lower() for p in re.split(r"[,;]", val) if p.strip()]
        return []

    def safe_str(val: Any) -> str:
        if isinstance(val, dict):
            return (val.get("details") or "").strip()
        return (val or "").strip()

    allergies = safe_list(dietary.get("food_allergies", {}))
    intolerances = safe_list(dietary.get("food_intolerances", {}))
    disliked_raw = dietary.get("disliked_foods", "")
    disliked = safe_list(disliked_raw) if disliked_raw else []
    favorites_raw = dietary.get("favorite_foods", "")
    favorites = safe_list(favorites_raw) if favorites_raw else []
    diet_types = safe_list(dietary.get("diet_types", {}))
    medications = safe_str(medical.get("medications", ""))

    return {
        "allergies": allergies,
        "intolerances": intolerances,
        "disliked": disliked,
        "favorites": favorites,
        "diet_types": diet_types,
        "medications": medications,
        "dee_goal": patient.get("dee_goal"),
        "macros": patient.get("macronutrient_distribution_in_grams", {}),
    }


def _text_contains_any(text: str, keywords: list[str]) -> bool:
    """Check if text contains any of the keywords (case-insensitive)."""
    if not text or not keywords:
        return False
    text_lower = text.lower()
    for kw in keywords:
        if kw and kw.lower() in text_lower:
            return True
    return False


def fetch_nutrition_estimate(food_name: str) -> dict[str, float] | None:
    """Try to get protein/carbs/fat from Open Food Facts search (best-effort for restaurant food)."""
    try:
        words = (food_name or "").split()[:3]
        q = urllib.parse.quote(" ".join(words) if words else "food")
        url = f"https://world.openfoodfacts.org/api/v2/search?search_terms={q}&search_simple=1&page_size=1"
        r = requests.get(url, timeout=5)
        if r.status_code != 200:
            return None
        data = r.json()
        products = data.get("products") or []
        if not products:
            return None
        nut = products[0].get("nutriments") or {}
        protein = nut.get("proteins_100g") or nut.get("proteins")
        carbs = nut.get("carbohydrates_100g") or nut.get("carbohydrates")
        fat = nut.get("fat_100g") or nut.get("fat")
        if protein is None and carbs is None and fat is None:
            return None
        return {
            "protein": round(float(protein or 0), 1),
            "carbohydrate": round(float(carbs or 0), 1),
            "fat": round(float(fat or 0), 1),
        }
    except Exception:
        return None


def filter_menu_item(item: dict[str, Any], constraints: dict[str, Any]) -> tuple[bool, str]:
    """
    Filter a menu item by dietary constraints.
    Returns (passes_filter, reason).
    """
    name = (item.get("name") or "").strip()
    desc = (item.get("description") or "").strip()
    combined = f"{name} {desc}".lower()

    # Hard exclusions - allergies and intolerances
    for allergen in constraints.get("allergies", []) + constraints.get("intolerances", []):
        if allergen and allergen in combined:
            return False, f"Contains allergen/intolerance: {allergen}"

    # Disliked foods - exclude
    for dislike in constraints.get("disliked", []):
        if dislike and dislike in combined:
            return False, f"Contains disliked food: {dislike}"

    return True, "OK"


def score_menu_item(item: dict[str, Any], constraints: dict[str, Any]) -> float:
    """Score item 0-100. Higher = better fit for user."""
    score = 50.0  # baseline
    name = (item.get("name") or "").lower()
    desc = (item.get("description") or "").lower()
    combined = f"{name} {desc}"

    # Boost for favorites
    for fav in constraints.get("favorites", []):
        if fav and fav in combined:
            score += 15
            break

    # Boost for healthy keywords
    healthy_keywords = [
        "salada", "salad", "grilled", "grelhado", "vegetal", "vegetable",
        "poke", "sopa", "soup", "fruta", "fruit", "arroz", "rice",
        "peixe", "fish", "frango", "chicken", "legumes", "vegetarian",
        "vegan", "plant", "açaí", "acai", "quinoa", "integral", "whole"
    ]
    for kw in healthy_keywords:
        if kw in combined:
            score += 5
            break

    return min(100, score)


def scrape_healthy_restaurants(
    city_slug: str = "braga-norte",
    category: str = "healthy",
    max_restaurants: int = 5,
    headless: bool = True,
) -> list[dict[str, Any]]:
    """Scrape restaurant list from Uber Eats healthy category using Playwright."""
    url = f"{UBER_PT}/category/{city_slug}/{category}"
    restaurants = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            locale="pt-PT",
        )
        page = context.new_page()

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=20000)
            time.sleep(3)

            # Accept cookies if dialog appears
            try:
                accept_btn = page.get_by_role("button", name="Accept")
                if accept_btn.is_visible(timeout=2000):
                    accept_btn.click()
                    time.sleep(1)
            except Exception:
                pass

            # Use JS to extract store links - more reliable than BeautifulSoup for SPA
            stores = page.evaluate("""
                () => {
                    const links = Array.from(document.querySelectorAll('a[href*="/store/"]'));
                    const seen = new Set();
                    return links
                        .map(a => {
                            const href = a.href || a.getAttribute('href');
                            if (!href || !href.includes('/store/') || seen.has(href)) return null;
                            seen.add(href);
                            const name = (a.querySelector('h3, h4, [role="heading"]') || a).innerText?.trim() || a.innerText?.trim() || 'Restaurant';
                            return { name: name.split('\\n')[0].slice(0, 80), url: href };
                        })
                        .filter(Boolean);
                }
            """)
            restaurants = stores[:max_restaurants] if stores else []
        finally:
            browser.close()

    return restaurants


def scrape_restaurant_menu(
    store_url: str,
    headless: bool = True,
) -> list[dict[str, Any]]:
    """Scrape menu items from a single Uber Eats store using Playwright."""
    menu_items = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            locale="pt-PT",
        )
        page = context.new_page()

        try:
            page.goto(store_url, wait_until="domcontentloaded", timeout=20000)
            time.sleep(3)

            # Accept cookies
            try:
                accept_btn = page.get_by_role("button", name="Accept")
                if accept_btn.is_visible(timeout=2000):
                    accept_btn.click()
                    time.sleep(1)
            except Exception:
                pass

            # Scroll to load lazy content
            for _ in range(6):
                page.mouse.wheel(0, 800)
                time.sleep(0.6)

            # Extract menu items via JS - name, price, image, description
            items = page.evaluate("""
                () => {
                    const results = [];
                    const skipStarts = ['Can I', 'How do', 'Where can', 'Is ', 'Learn '];
                    const priceRe = /[€$]\\s*[\\d,]+\\.?\\d*/;
                    document.querySelectorAll('[data-testid="menu-item-title"]').forEach(el => {
                        const name = el.innerText?.trim();
                        if (!name || name.length < 2 || skipStarts.some(s => name.startsWith(s))) return;
                        const parent = el.closest('li, [role="listitem"], div[class*="item"]');
                        let desc = '', img = null, price = null;
                        if (parent) {
                            const fullText = parent.innerText || '';
                            const pm = fullText.match(priceRe);
                            price = pm ? pm[0].trim() : null;
                            const p = parent.querySelector('p, [class*="desc"], [class*="description"]');
                            desc = p?.innerText?.trim() || '';
                            const im = parent.querySelector('img');
                            img = im?.src || im?.getAttribute('data-src') || null;
                        }
                        results.push({ name, description: desc || null, image_url: img, price });
                    });
                    if (results.length === 0) {
                        document.querySelectorAll('li, [role="listitem"]').forEach(li => {
                            const h = li.querySelector('h1, h2, h3, h4, h5, h6');
                            const name = h?.innerText?.trim() || li.querySelector('[class*="title"]')?.innerText?.trim();
                            if (!name || name.length < 2 || skipStarts.some(s => name.startsWith(s))) return;
                            const fullText = li.innerText || '';
                            const pm = fullText.match(priceRe);
                            const im = li.querySelector('img');
                            results.push({
                                name: name.slice(0, 200),
                                description: li.querySelector('p')?.innerText?.trim()?.slice(0, 500) || null,
                                image_url: im?.src || im?.getAttribute('data-src') || null,
                                price: pm ? pm[0].trim() : null
                            });
                        });
                    }
                    return results;
                }
            """)
            menu_items = items or []
        finally:
            browser.close()

    # Deduplicate and filter out section headers / promo text
    skip_patterns = (
        "save on", "buy 1", "get 1", "offer", "top offer", "spend €", "appetisers",
        "sides", "drinks", "bebidas", "sobremesas", "desserts", "house news",
        "sweet treats", "sweet bowls", "extras", "add-ons", "combos",
        "featured items", "entradas", "hambúrgueres", "loja", "taxa de embalagem",
        "packaging", "fee", "limite", "limitado",
    )
    seen = set()
    unique = []
    for m in menu_items:
        name = (m.get("name") or "").strip()
        key = name.lower()
        if not key or key in seen:
            continue
        if any(p in key for p in skip_patterns) or len(name) < 4:
            continue
        seen.add(key)
        unique.append(m)

    return unique


def find_food_for_patient(
    patient: dict[str, Any],
    city_slug: str = "braga-norte",
    max_restaurants: int = 3,
    max_items_per_restaurant: int = 20,
    headless: bool = True,
) -> list[dict[str, Any]]:
    """
    Main pipeline: load patient → scrape healthy restaurants → scrape menus → filter by diet → score.
    Returns ranked list of food items that fit the patient's dietary constraints.
    """
    constraints = extract_dietary_constraints(patient)
    patient_name = patient.get("patient_name", "Unknown")

    restaurants = scrape_healthy_restaurants(
        city_slug=city_slug,
        max_restaurants=max_restaurants,
        headless=headless,
    )

    all_items: list[dict[str, Any]] = []

    for rest in restaurants:
        items = scrape_restaurant_menu(rest["url"], headless=headless)
        for item in items[:max_items_per_restaurant]:
            passes, reason = filter_menu_item(item, constraints)
            if passes:
                score = score_menu_item(item, constraints)
                all_items.append({
                    **item,
                    "restaurant": rest["name"],
                    "restaurant_url": rest["url"],
                    "score": score,
                    "patient": patient_name,
                })

    # Sort by score descending
    all_items.sort(key=lambda x: x["score"], reverse=True)

    # Enrich top items with nutrition estimate from Open Food Facts (best-effort)
    for i, item in enumerate(all_items[:10]):
        if not item.get("macronutrient_distribution_in_grams"):
            nut = fetch_nutrition_estimate(item.get("name", ""))
            if nut:
                item["macronutrient_distribution_in_grams"] = nut
            time.sleep(0.7)  # Rate limit

    return all_items


def run_from_jsonl(
    jsonl_path: str | Path,
    city_slug: str = "braga-norte",
    patient_index: int = 0,
    headless: bool = True,
) -> list[dict[str, Any]]:
    """Convenience: load patient from JSONL and find food."""
    patients = load_patient_diet(jsonl_path)
    if not patients:
        raise ValueError("No patients found in file")
    patient = patients[min(patient_index, len(patients) - 1)]
    return find_food_for_patient(patient, city_slug=city_slug, headless=headless)


if __name__ == "__main__":
    import sys

    data_path = Path(__file__).parent / "data" / "input_nutri_approval (3).jsonl"
    if len(sys.argv) > 1:
        data_path = Path(sys.argv[1])
    patient_idx = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    city = sys.argv[3] if len(sys.argv) > 3 else "braga-norte"

    print(f"Loading patient from {data_path} (index {patient_idx})...")
    results = run_from_jsonl(data_path, city_slug=city, patient_index=patient_idx, headless=True)

    print(f"\n=== Found {len(results)} food items that fit your diet ===\n")
    for i, item in enumerate(results[:15], 1):
        print(f"{i}. {item['name']} @ {item['restaurant']} (score: {item['score']:.0f})")
        if item.get("description"):
            print(f"   {item['description'][:100]}...")
        print(f"   Order: {item['restaurant_url']}\n")
