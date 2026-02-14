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
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import requests
from playwright.sync_api import sync_playwright

# Uber Eats URL structure
UBER_PREFIX = "https://www.ubereats.com"
UBER_PT = "https://www.ubereats.com/pt-en"
SHOP_FEED_URL = "https://www.ubereats.com/feeds/shop_feed"
# Default grocery store for Braga (salads, prepared food, healthy sandwiches)
CONTINENTE_BRAGA_URL = "https://www.ubereats.com/store/continente-bom-dia-braga-oficinas/BONZWzrmSnOr26sNmYfjhA?diningMode=DELIVERY"
# Prepared Food category (salads, sandwiches) - direct link for better scraping
CONTINENTE_PREPARED_FOOD_URL = "https://www.ubereats.com/store/continente-bom-dia-braga-oficinas/BONZWzrmSnOr26sNmYfjhA/04e3595b-3ae6-4a73-abdb-ab0d9987e384/4251c055-eace-4db2-a18c-686924397f24?diningMode=DELIVERY&ps=1&scats=4251c055-eace-4db2-a18c-686924397f24&scatsectypes=COLLECTION&scatsubs=48939e3d-3260-41e5-8098-119039642685"


def _fetch_single_uber_eats_image(item_name: str, headless: bool = True) -> str | None:
    """Fetch image URL for one item from Uber Eats. Used by concurrent workers."""
    name = (item_name or "").strip()
    if not name:
        return None
    # Try feed with search query first (often works better), then shop_feed
    urls_to_try = [
        f"{UBER_PT}/feed?q={urllib.parse.quote(name[:40])}",
        SHOP_FEED_URL,
        f"{UBER_PT}/feed",
    ]
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=headless)
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                locale="pt-PT",
            )
            page = context.new_page()
            try:
                for url in urls_to_try:
                    try:
                        page.goto(url, wait_until="domcontentloaded", timeout=12000)
                        time.sleep(1.5)
                        break
                    except Exception:
                        continue
                try:
                    accept_btn = page.get_by_role("button", name="Accept")
                    if accept_btn.is_visible(timeout=1500):
                        accept_btn.click()
                        time.sleep(0.5)
                except Exception:
                    pass

                # If we didn't use search URL, try to search on page
                if "q=" not in page.url:
                    search_el = page.get_by_placeholder("Search", exact=False).or_(
                        page.locator('input[type="search"], input[aria-label*="search" i], input[placeholder*="search" i], input[placeholder*="Pesquisar" i]')
                    ).first
                    if search_el.is_visible(timeout=1500):
                        search_el.fill(name[:50])
                        time.sleep(1.5)

                for _ in range(3):
                    page.mouse.wheel(0, 300)
                    time.sleep(0.3)

                img_url = page.evaluate("""
                    () => {
                        const skip = /logo|icon|avatar|badge|placeholder|\\.svg|_static\\//i;
                        const imgs = document.querySelectorAll('img[src]');
                        for (const im of imgs) {
                            const src = im.src || im.getAttribute('data-src');
                            if (!src || !src.startsWith('http') || src.endsWith('.svg')) continue;
                            if (skip.test(src) || skip.test(im.alt || '')) continue;
                            if (src.includes('cloudfront') || src.includes('tb-static') || src.includes('d1a3f4spazzrp4') || src.includes('d3i4yxtzktqr9n') || src.match(/\\d{10,}\\.(jpg|jpeg|png|webp)/i)) {
                                return src;
                            }
                        }
                        for (const im of imgs) {
                            const src = im.src || im.getAttribute('data-src');
                            if (src && src.startsWith('http') && !src.endsWith('.svg') && !skip.test(src)) {
                                const parent = im.closest('a[href*="/store/"], a[href*="/product/"], [class*="card"], [class*="Card"]');
                                if (parent && (src.includes('cloudfront') || src.includes('jpeg') || src.includes('jpg') || src.includes('png'))) return src;
                            }
                        }
                        return null;
                    }
                """)
                return img_url
            finally:
                browser.close()
    except Exception:
        pass
    return None


def fetch_uber_eats_images_for_items(
    items: list[dict[str, Any]],
    headless: bool = True,
    max_workers: int = 4,
) -> None:
    """
    Fetch image_url from Uber Eats for items that don't have one.
    Runs concurrently (ThreadPoolExecutor) for speed.
    Mutates items in place.
    """
    to_fetch = [(i, it) for i, it in enumerate(items) if not it.get("image_url") and (it.get("name") or "").strip()]
    if not to_fetch:
        return

    def _task(args):
        idx, it = args
        url = _fetch_single_uber_eats_image(it.get("name"), headless=headless)
        return idx, it, url

    with ThreadPoolExecutor(max_workers=min(max_workers, len(to_fetch))) as ex:
        futures = {ex.submit(_task, item): item for item in to_fetch}
        for future in as_completed(futures, timeout=30):
            try:
                idx, it, url = future.result()
                if url:
                    it["image_url"] = url
            except Exception:
                pass


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
    detail = fetch_nutrition_detail(food_name)
    if not detail or not detail.get("nutriments"):
        return None
    nut = detail["nutriments"]
    if not any(nut.get(k) for k in ("protein", "carbohydrate", "fat")):
        return None
    return {
        "protein": round(float(nut.get("protein", 0) or 0), 1),
        "carbohydrate": round(float(nut.get("carbohydrate", 0) or 0), 1),
        "fat": round(float(nut.get("fat", 0) or 0), 1),
    }


def enrich_item_with_nutrition(item: dict[str, Any]) -> None:
    """Add full nutriments (energy_kcal, protein, carbs, fat, etc.) to item for instant display."""
    if item.get("nutriments"):
        return
    from nutrition_local_db import get_nutrition_for_serving

    detail = get_nutrition_for_serving(item.get("name", ""), restaurant=item.get("restaurant"))
    if detail and detail.get("nutriments"):
        item["nutriments"] = detail["nutriments"]
        item["nutrition_source"] = detail.get("source", "local_db")
        nut = detail["nutriments"]
        if not item.get("macronutrient_distribution_in_grams"):
            item["macronutrient_distribution_in_grams"] = {
                "protein": nut.get("protein"),
                "carbohydrate": nut.get("carbohydrate"),
                "fat": nut.get("fat"),
            }
        return


def enrich_items_parallel(items: list[dict[str, Any]], max_workers: int = 8) -> None:
    """Enrich multiple items with nutrition in parallel. Much faster than sequential."""
    to_enrich = [i for i in items if not i.get("nutriments")]
    if not to_enrich:
        return
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        list(ex.map(enrich_item_with_nutrition, to_enrich))


_DRINK_KEYWORDS = frozenset(
    "water eau água mineral aqua soda cola coke pepsi juice sumo refrigerante "
    "bebida drink cerveja beer vinho wine licor vodka whisky".split()
)


def _score_product_match(product: dict[str, Any], search_words: list[str]) -> float:
    """Score how well a product matches the search. Higher = better match."""
    name = (product.get("product_name") or "").lower()
    nut = product.get("nutriments") or {}
    # Strong penalty for drinks/water
    if any(kw in name for kw in _DRINK_KEYWORDS):
        return -100
    # Name match: count search words found in product name
    name_score = sum(1 for w in search_words if w and len(w) > 1 and w.lower() in name)
    # Completeness: prefer products with real nutrition data
    has_protein = (nut.get("proteins_100g") or nut.get("proteins")) is not None
    has_carbs = (nut.get("carbohydrates_100g") or nut.get("carbohydrates")) is not None
    has_fat = (nut.get("fat_100g") or nut.get("fat")) is not None
    has_energy = (
        (nut.get("energy-kcal_100g") or nut.get("energy-kcal") or nut.get("energy-kcal_value_computed"))
        is not None
    )
    completeness = sum([has_protein, has_carbs, has_fat, has_energy])
    # Prefer products with actual macros (avoid water/zero)
    p = float(nut.get("proteins_100g") or nut.get("proteins") or 0)
    c = float(nut.get("carbohydrates_100g") or nut.get("carbohydrates") or 0)
    f = float(nut.get("fat_100g") or nut.get("fat") or 0)
    total = p + c + f
    substance = min(5, total)  # scale 0-5 by total macros
    return name_score * 15 + completeness * 3 + substance


def _fetch_nutrition_from_products(products: list[dict], words: list[str]) -> dict[str, Any] | None:
    """Extract nutrition from best-matching product. Returns None if all are drinks."""
    if not products:
        return None
    scored = [(pr, _score_product_match(pr, words)) for pr in products]
    best, score = max(scored, key=lambda x: x[1])
    if score < 0:
        return None
    p = best
    nut = p.get("nutriments") or {}
    result: dict[str, Any] = {
        "product_name": p.get("product_name"),
        "nutriments": {},
        "source": "openfoodfacts",
    }
    for key, off_keys in [
        ("energy_kcal", ["energy-kcal_100g", "energy-kcal", "energy-kcal_value_computed"]),
        ("protein", ["proteins_100g", "proteins"]),
        ("carbohydrate", ["carbohydrates_100g", "carbohydrates"]),
        ("fat", ["fat_100g", "fat"]),
        ("fiber", ["fiber_100g", "fiber"]),
        ("sugar", ["sugars_100g", "sugars"]),
        ("sodium", ["sodium_100g", "sodium"]),
        ("salt", ["salt_100g", "salt"]),
    ]:
        if key not in result["nutriments"]:
            val = None
            for k in off_keys:
                v = nut.get(k)
                if v is not None:
                    if key == "energy_kcal" and float(v) == 0 and k != off_keys[-1]:
                        continue
                    val = v
                    break
            if val is not None:
                result["nutriments"][key] = round(float(val), 1)
    return result if result["nutriments"] else None


def fetch_nutrition_detail(food_name: str) -> dict[str, Any] | None:
    """Get full nutrition info from Open Food Facts. Picks best-matching product by name + completeness."""
    try:
        words = [w for w in (food_name or "").split() if len(w) > 1][:5]
        if not words:
            return None
        q = urllib.parse.quote(" ".join(words))
        url = f"https://world.openfoodfacts.org/api/v2/search?search_terms={q}&search_simple=1&page_size=10&fields=product_name,nutriments,energy-kcal_100g"
        r = requests.get(url, timeout=6)
        if r.status_code != 200:
            return None
        data = r.json()
        products = data.get("products") or []
        detail = _fetch_nutrition_from_products(products, words)
        if detail:
            return detail
        # Fallback: try with just first word (e.g. "salada" for "Salada mista")
        if len(words) > 1:
            fallback_q = urllib.parse.quote(words[0])
            fallback_url = f"https://world.openfoodfacts.org/api/v2/search?search_terms={fallback_q}&search_simple=1&page_size=15&fields=product_name,nutriments,energy-kcal_100g"
            r2 = requests.get(fallback_url, timeout=6)
            if r2.status_code == 200:
                products2 = (r2.json() or {}).get("products") or []
                return _fetch_nutrition_from_products(products2, words)
        return None
    except Exception:
        return None


def _is_drink(name: str, description: str = "") -> bool:
    """Return True if item appears to be a drink (exclude from food results)."""
    combined = f"{(name or '').strip()} {(description or '').strip()}".lower()
    drink_kw = [
        "café", "coffee", "chá", "tea", "sumo", "juice", "refrigerante", "soda", "cola", "coca",
        "cerveja", "beer", "wine", "vinho", "água", "water", "smoothie", "leite", "milk",
        "bebida", "drink", "sprite", "fanta", "red bull", "energético", "energy drink",
        "mocktail", "cocktail", "sangria", "limonada", "lemonade", "ice tea", "chá gelado",
        "expresso", "espresso", "cappuccino", "latte", "mocha", "água com gás", "sparkling",
        "sumo de", "juice of", "copo de", "glass of", "garrafa de", "bottle of",
    ]
    return any(kw in combined for kw in drink_kw)


def load_continente_grocery_from_all_menus(
    filepath: str | Path | None = None,
    max_items: int = 60,
) -> list[dict[str, Any]]:
    """
    Load Continente Braga grocery items from all_menus.json.
    Focus on salads, prepared food, sandwiches, produce. Returns items with product_url for ordering.
    """
    path = Path(filepath or (Path(__file__).parent / "all_menus.json"))
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            return []
        healthy_kw = ["salada", "salad", "sandes", "sandwich", "preparad", "legumes", "fruta", "vegetal", "bio", "integral", "grelhado", "atum", "frango", "queijo", "iogurte", "arroz", "massa", "banana", "uva", "abacate", "mirtilo", "maçã", "laranja", "manga", "limão", "clementina", "frutas", "produce"]
        exclude_kw = ["coloração", "perfume", "batom", "verniz", "unhas", "máscara", "eyeliner", "lápis de olhos", "beauty", "cosmetic", "nail", "hair", "ração", "cão", "gato", "pet", "dog", "cat"]
        items: list[dict[str, Any]] = []
        for store in data:
            url = store.get("url") or ""
            if "continente-bom-dia-braga" not in url.lower():
                continue
            store_name = "Continente Bom Dia Braga"
            for m in store.get("menu") or []:
                name = (m.get("name") or "").strip()
                desc = (m.get("description") or "").strip()
                if not name or len(name) < 3:
                    continue
                if name.startswith("€") or re.match(r"^[€$]\d", name) or name.startswith("(est)"):
                    ext = re.search(r"[€$]\s*[\d,]+\.?\d*(?:\(est\))?\s*(.+)", desc)
                    name = (ext.group(1).strip() if ext else desc.split("Quick view")[-1].strip())[:100]
                    name = re.sub(r"^\(est\)", "", name).strip()
                if not name or len(name) < 4:
                    continue
                name_lower = name.lower()
                if any(skip in name_lower for skip in ("featured", "most liked", "see all", "for all your", "frutas da época")):
                    continue
                if name_lower in ("deli", "produce", "prepared food", "all items"):
                    continue
                if any(ex in name_lower for ex in exclude_kw):
                    continue
                if _is_drink(name, desc):
                    continue
                score = sum(1 for kw in healthy_kw if kw in name_lower or kw in desc.lower())
                if score < 1:
                    continue
                items.append({
                    "name": name[:120],
                    "description": desc[:200],
                    "price": m.get("price"),
                    "image_url": m.get("image_url"),
                    "restaurant": store_name,
                    "restaurant_url": url.split("?")[0],
                    "product_url": m.get("product_url"),
                    "store_url": url.split("?")[0],
                    "from_all_menus": True,
                    "healthy_score": score,
                })
                if len(items) >= max_items * 2:
                    break
            if items:
                break
        items.sort(key=lambda x: x.get("healthy_score", 0), reverse=True)
        return items[:max_items]
    except Exception:
        return []


def load_all_menus_items(
    filepath: str | Path | None = None,
    max_items: int = 800,
) -> list[dict[str, Any]]:
    """Load items from all_menus.json for diverse selection. Returns flat list with restaurant_url, store name."""
    path = Path(filepath or (Path(__file__).parent / "all_menus.json"))
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            return []
        items: list[dict[str, Any]] = []
        for store in data:
            if len(items) >= max_items:
                break
            url = store.get("url") or ""
            store_name = url.split("/store/")[-1].split("/")[0] if "/store/" in url else "Uber Eats"
            store_name = store_name.replace("-", " ").title()
            for m in store.get("menu") or []:
                name = (m.get("name") or "").strip()
                if not name or len(name) < 3:
                    continue
                name_lower = name.lower()
                if any(skip in name_lower for skip in ("featured", "most liked", "combos", "section", "#1 ", "#2 ", "#3 ")):
                    continue
                if name_lower.startswith("#") and name_lower[1:2].isdigit():
                    continue
                desc = (m.get("description") or "")[:300]
                if _is_drink(name, desc):
                    continue
                items.append({
                    "name": name[:120],
                    "description": desc,
                    "price": m.get("price"),
                    "image_url": m.get("image_url"),
                    "restaurant": store_name,
                    "restaurant_url": url,
                    "product_url": m.get("product_url"),
                    "from_all_menus": True,
                })
                if len(items) >= max_items:
                    break
        return items
    except Exception:
        return []


def filter_menu_item(item: dict[str, Any], constraints: dict[str, Any]) -> tuple[bool, str]:
    """
    Filter a menu item by dietary constraints.
    Returns (passes_filter, reason).
    """
    name = (item.get("name") or "").strip()
    desc = (item.get("description") or "").strip()
    combined = f"{name} {desc}".lower()

    # Exclude drinks
    if _is_drink(name, desc):
        return False, "Drink (excluded)"

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
                    const priceRe = /[€$]\s*[\d,]+\.?\d*/;
                    document.querySelectorAll('[data-testid="menu-item-title"]').forEach(el => {
                        const name = el.innerText?.trim();
                        if (!name || name.length < 2 || skipStarts.some(s => name.startsWith(s))) return;
                        const parent = el.closest('li, [role="listitem"], div[class*="item"]');
                        let desc = '', img = null, price = null, product_url = null;
                        if (parent) {
                            const fullText = parent.innerText || '';
                            const pm = fullText.match(priceRe);
                            price = pm ? pm[0].trim() : null;
                            const p = parent.querySelector('p, [class*="desc"], [class*="description"]');
                            desc = p?.innerText?.trim() || '';
                            const im = parent.querySelector('img');
                            img = im?.src || im?.getAttribute('data-src') || null;
                            // Try to find a product link
                            const a = parent.querySelector('a[href*="/store/"]');
                            if (a) {
                                let href = a.href || a.getAttribute('href');
                                if (href && href.startsWith('/')) {
                                    href = 'https://www.ubereats.com' + href;
                                }
                                product_url = href;
                            }
                        }
                        results.push({ name, description: desc || null, image_url: img, price, product_url: product_url || null });
                    });
                    if (results.length === 0) {
                        document.querySelectorAll('li, [role="listitem"]').forEach(li => {
                            const h = li.querySelector('h1, h2, h3, h4, h5, h6');
                            const name = h?.innerText?.trim() || li.querySelector('[class*="title"]')?.innerText?.trim();
                            if (!name || name.length < 2 || skipStarts.some(s => name.startsWith(s))) return;
                            const fullText = li.innerText || '';
                            const pm = fullText.match(priceRe);
                            const im = li.querySelector('img');
                            // Try to find a product link
                            let product_url = null;
                            const a = li.querySelector('a[href*="/store/"]');
                            if (a) {
                                let href = a.href || a.getAttribute('href');
                                if (href && href.startsWith('/')) {
                                    href = 'https://www.ubereats.com' + href;
                                }
                                product_url = href;
                            }
                            results.push({
                                name: name.slice(0, 200),
                                description: li.querySelector('p')?.innerText?.trim()?.slice(0, 500) || null,
                                image_url: im?.src || im?.getAttribute('data-src') || null,
                                price: pm ? pm[0].trim() : null,
                                product_url: product_url || null
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


# Grocery basket composition - meal structure
BASKET_MEAL_STRUCTURE = {
    "protein": ["frango", "chicken", "peixe", "fish", "carne", "meat", "ovo", "egg", "atum", "tuna", "peru", "turkey", "tofu", "grilled", "grelhado"],
    "carbohydrate": ["arroz", "rice", "batata", "potato", "massa", "pasta", "pão", "bread", "quinoa", "integral", "whole"],
    "vegetable": ["salada", "salad", "vegetal", "vegetable", "legumes", "sopa", "soup", "brócolos", "broccoli", "espinafre", "spinach"],
    "fruit": ["fruta", "fruit", "maçã", "apple", "banana", "laranja", "orange"],
    "drink": ["água", "water", "sumo", "juice", "chá", "tea", "leite", "milk"],
}


def _item_matches_category(item: dict[str, Any], keywords: list[str]) -> bool:
    """Check if item name/description matches any keyword in category."""
    name = (item.get("name") or "").lower()
    desc = (item.get("description") or "").lower()
    combined = f"{name} {desc}"
    return any(kw in combined for kw in keywords)


def compose_healthy_basket(
    items: list[dict[str, Any]],
    constraints: dict[str, Any],
    max_items: int = 6,
) -> list[dict[str, Any]]:
    """
    Compose a balanced meal basket from filtered items.
    Picks: 1 protein, 1 carb, 1+ veg, optional fruit/drink.
    Respects allergies, favors favorites.
    """
    basket: list[dict[str, Any]] = []
    used_names: set[str] = set()

    def pick_one(category: str) -> dict[str, Any] | None:
        keywords = BASKET_MEAL_STRUCTURE.get(category, [])
        candidates = [
            i for i in items
            if _item_matches_category(i, keywords)
            and (i.get("name") or "").strip().lower() not in used_names
        ]
        if not candidates:
            return None
        # Prefer higher score (favorites)
        candidates.sort(key=lambda x: x.get("score", 50), reverse=True)
        chosen = candidates[0]
        used_names.add((chosen.get("name") or "").strip().lower())
        return chosen

    # 1. Protein
    p = pick_one("protein")
    if p:
        basket.append({**p, "basket_role": "protein"})

    # 2. Carbohydrate
    c = pick_one("carbohydrate")
    if c:
        basket.append({**c, "basket_role": "carbohydrate"})

    # 3. Vegetable
    v = pick_one("vegetable")
    if v:
        basket.append({**v, "basket_role": "vegetable"})

    # 4. Optional second veg or fruit
    v2 = pick_one("vegetable") or pick_one("fruit")
    if v2 and len(basket) < max_items:
        basket.append({**v2, "basket_role": "vegetable_or_fruit"})

    # Drinks excluded per user preference

    # If we have very few items, add high-scoring items we missed
    if len(basket) < 3:
        for item in sorted(items, key=lambda x: x.get("score", 0), reverse=True):
            if len(basket) >= max_items:
                break
            key = (item.get("name") or "").strip().lower()
            if key and key not in used_names:
                basket.append({**item, "basket_role": "extra"})
                used_names.add(key)

    return basket


def scrape_grocery_stores_from_shop_feed(headless: bool = True) -> list[dict[str, Any]]:
    """
    Go to shop_feed, extract grocery store links. Returns [{name, url}].
    """
    stores: list[dict[str, Any]] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            locale="pt-PT",
        )
        page = context.new_page()
        try:
            page.goto(SHOP_FEED_URL, wait_until="domcontentloaded", timeout=25000)
            time.sleep(4)
            try:
                accept_btn = page.get_by_role("button", name="Accept")
                if accept_btn.is_visible(timeout=2000):
                    accept_btn.click()
                    time.sleep(1)
            except Exception:
                pass
            for _ in range(4):
                page.mouse.wheel(0, 500)
                time.sleep(0.4)
            stores = page.evaluate("""
                () => {
                    const seen = new Set();
                    const skip = /android|play\\.google|app store|get it on/i;
                    return Array.from(document.querySelectorAll('a[href*="ubereats.com/store/"]'))
                        .map(a => {
                            const href = a.href || a.getAttribute('href');
                            if (!href || !href.includes('ubereats.com/store/') || seen.has(href.split('?')[0])) return null;
                            const name = (a.querySelector('h2, h3, h4, [role="heading"]') || a).innerText?.trim()?.split('\\n')[0] || '';
                            if (!name || skip.test(name) || name.length < 3) return null;
                            seen.add(href.split('?')[0]);
                            return { name: name.slice(0, 60), url: href };
                        })
                        .filter(Boolean);
                }
            """)
            stores = stores or []
        except Exception:
            pass
        finally:
            browser.close()
    return stores[:15]


def scrape_store_healthy_products(
    store_url: str,
    store_name: str = "Grocery Store",
    headless: bool = True,
    max_items: int = 40,
) -> tuple[list[dict[str, Any]], str]:
    """
    Go to a grocery store, find Prepared Food / Deli / Produce, scrape salads & healthy items.
    Returns (items, store_url) with real product URLs for ordering.
    """
    items: list[dict[str, Any]] = []
    base_url = store_url.split("?")[0].rstrip("/")
    healthy_keywords = ["prepared", "preparad", "delicatessen", "deli", "produce", "salad", "sandwich", "sandes", "salada"]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            locale="pt-PT",
        )
        page = context.new_page()
        try:
            page.goto(store_url, wait_until="domcontentloaded", timeout=25000)
            time.sleep(4)
            try:
                accept_btn = page.get_by_role("button", name="Accept")
                if accept_btn.is_visible(timeout=2000):
                    accept_btn.click()
                    time.sleep(1)
            except Exception:
                pass

            # Find category links (Prepared Food, Deli, Produce, etc.)
            category_links = page.evaluate("""
                (keywords) => {
                    const links = [];
                    document.querySelectorAll('a[href*="/store/"]').forEach(a => {
                        const href = a.href || a.getAttribute('href');
                        const text = (a.innerText || a.textContent || '').toLowerCase();
                        if (!href || !href.includes('/store/')) return;
                        const segs = href.split('/').filter(Boolean);
                        if (segs.length < 6) return;
                        for (const kw of keywords) {
                            if (text.includes(kw)) {
                                links.push({ href, text });
                                return;
                            }
                        }
                    });
                    return links.slice(0, 5);
                }
            """, healthy_keywords)

            urls_to_scrape = [store_url]
            for cl in (category_links or []):
                h = cl.get("href") or ""
                if h and h not in urls_to_scrape:
                    urls_to_scrape.append(h)
            if "continente" in store_url.lower() and "braga" in store_url.lower():
                urls_to_scrape.insert(1, CONTINENTE_PREPARED_FOOD_URL)

            seen_names: set[str] = set()
            for url in urls_to_scrape[:3]:
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=20000)
                    time.sleep(3)
                    for _ in range(5):
                        page.mouse.wheel(0, 400)
                        time.sleep(0.3)
                    batch = page.evaluate("""
                        () => {
                            const results = [];
                            const priceRe = /[€$]\\s*[\\d,]+\\.?\\d*/;
                            document.querySelectorAll('a[href*="/store/"], [data-testid*="item"], [class*="ProductCard"], [class*="MenuItem"], [class*="item"]').forEach(el => {
                                const a = el.tagName === 'A' ? el : el.querySelector('a[href*="/store/"]');
                                const href = a?.href || el.querySelector('a')?.href;
                                if (!href || !href.includes('/store/')) return;
                                const fullText = (el.innerText || '').trim();
                                const lines = fullText.split('\\n').map(s => s.trim()).filter(Boolean);
                                let name = '';
                                let price = null;
                                for (const line of lines) {
                                    const pm = line.match(priceRe);
                                    if (pm) { price = pm[0].trim(); continue; }
                                    if (line.length > 2 && !/^\\d+$/.test(line) && !/^[€$]/.test(line)) {
                                        if (!name) name = line;
                                        else break;
                                    }
                                }
                                if (!name || name.length < 4) return;
                                if (/^[€$]\\d/.test(name)) return;
                                const im = el.querySelector('img');
                                results.push({
                                    name: name.slice(0, 120),
                                    description: null,
                                    image_url: im?.src || im?.getAttribute('data-src') || null,
                                    price: price,
                                    product_url: href
                                });
                            });
                            return results;
                        }
                    """)
                    skip_names = {"android", "continente", "info", "shop", "see all", "delivery", "store", "get", "download", "rating", "star"}
                    for m in batch or []:
                        name = (m.get("name") or "").strip()
                        key = name.lower()
                        if key and key not in seen_names and len(name) >= 4:
                            if key in skip_names or any(skip in key for skip in ("save on", "offer", "spend €", "taxa", "fee", "delivery", "see all")):
                                continue
                            seen_names.add(key)
                            m["restaurant"] = store_name
                            m["restaurant_url"] = base_url
                            m["store_url"] = base_url
                            items.append(m)
                            if len(items) >= max_items:
                                break
                    if len(items) >= max_items:
                        break
                except Exception:
                    continue

        except Exception:
            pass
        finally:
            browser.close()

    return items[:max_items], base_url


def scrape_shop_feed_healthy_items(
    constraints: dict[str, Any],
    headless: bool = True,
    max_items: int = 20,
) -> tuple[list[dict[str, Any]], str]:
    """
    Scrape healthy grocery items from Uber Eats shop feed.
    Uses Playwright to navigate to shop_feed, search for healthy foods.
    Returns (items, store_url). store_url is SHOP_FEED_URL for valid Order links.
    """
    items: list[dict[str, Any]] = []
    search_terms = ["healthy", "frango grelhado", "arroz integral", "salada", "legumes"]
    if constraints.get("favorites"):
        search_terms = list(constraints["favorites"])[:3] + search_terms
    search_terms = list(dict.fromkeys(search_terms))[:5]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            locale="pt-PT",
        )
        page = context.new_page()
        try:
            page.goto(SHOP_FEED_URL, wait_until="domcontentloaded", timeout=25000)
            time.sleep(4)

            try:
                accept_btn = page.get_by_role("button", name="Accept")
                if accept_btn.is_visible(timeout=2000):
                    accept_btn.click()
                    time.sleep(1)
            except Exception:
                pass

            for _ in range(4):
                page.mouse.wheel(0, 600)
                time.sleep(0.5)

            items = page.evaluate("""
                () => {
                    const results = [];
                    const priceRe = /[€$]\\s*[\\d,]+\\.?\\d*/;
                    document.querySelectorAll('a[href*="/store/"], a[href*="/product/"], [data-testid*="item"], [class*="ProductCard"]').forEach(el => {
                        const a = el.tagName === 'A' ? el : el.querySelector('a[href*="/store/"], a[href*="/product/"]');
                        const href = a?.href || el.getAttribute('href');
                        if (!href || !(href.includes('/store/') || href.includes('/product/'))) return;
                        const nameEl = el.querySelector('h1, h2, h3, h4, [class*="title"], [data-testid*="title"]');
                        const name = nameEl?.innerText?.trim() || el.innerText?.trim()?.split('\\n')[0];
                        if (!name || name.length < 3) return;
                        const fullText = el.innerText || '';
                        const pm = fullText.match(priceRe);
                        const im = el.querySelector('img');
                        results.push({
                            name: name.slice(0, 120),
                            description: null,
                            image_url: im?.src || im?.getAttribute('data-src') || null,
                            price: pm ? pm[0].trim() : null,
                            product_url: href
                        });
                    });
                    if (results.length === 0) {
                        document.querySelectorAll('li, [role="listitem"], [class*="card"]').forEach(li => {
                            const h = li.querySelector('h1, h2, h3, h4, h5, h6, [class*="title"]');
                            const name = h?.innerText?.trim() || li.innerText?.trim()?.split('\\n')[0];
                            if (!name || name.length < 3) return;
                            const im = li.querySelector('img');
                            const fullText = li.innerText || '';
                            const pm = fullText.match(priceRe);
                            results.push({
                                name: name.slice(0, 120),
                                description: null,
                                image_url: im?.src || im?.getAttribute('data-src') || null,
                                price: pm ? pm[0].trim() : null,
                                product_url: null
                            });
                        });
                    }
                    return results;
                }
            """)
            items = items or []
        except Exception:
            pass
        finally:
            browser.close()

    seen = set()
    unique = []
    for m in items:
        name = (m.get("name") or "").strip()
        key = name.lower()
        if not key or key in seen or len(name) < 4:
            continue
        if any(p in key for p in ("save on", "offer", "spend €", "appetisers", "taxa", "fee")):
            continue
        seen.add(key)
        m["restaurant_url"] = m.get("product_url") or SHOP_FEED_URL
        m["store_url"] = SHOP_FEED_URL
        unique.append(m)

    return unique[:max_items], SHOP_FEED_URL


def scrape_grocery_stores(
    city_slug: str = "braga-norte",
    category: str = "grocery",
    max_stores: int = 3,
    headless: bool = True,
) -> list[dict[str, Any]]:
    """Scrape grocery/supermarket stores from Uber Eats (same pattern as restaurants)."""
    url = f"{UBER_PT}/category/{city_slug}/{category}"
    stores = []

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

            try:
                accept_btn = page.get_by_role("button", name="Accept")
                if accept_btn.is_visible(timeout=2000):
                    accept_btn.click()
                    time.sleep(1)
            except Exception:
                pass

            stores = page.evaluate("""
                () => {
                    const links = Array.from(document.querySelectorAll('a[href*="/store/"]'));
                    const seen = new Set();
                    return links
                        .map(a => {
                            const href = a.href || a.getAttribute('href');
                            if (!href || !href.includes('/store/') || seen.has(href)) return null;
                            seen.add(href);
                            const name = (a.querySelector('h3, h4, [role="heading"]') || a).innerText?.trim() || a.innerText?.trim() || 'Store';
                            return { name: name.split('\\n')[0].slice(0, 80), url: href };
                        })
                        .filter(Boolean);
                }
            """)
            stores = stores[:max_stores] if stores else []
        except Exception:
            pass
        finally:
            browser.close()

    return stores


def find_grocery_basket_for_patient(
    patient: dict[str, Any],
    city_slug: str = "braga-norte",
    max_stores: int = 2,
    max_items_per_store: int = 30,
    headless: bool = True,
) -> dict[str, Any]:
    """
    Find a ready-to-order grocery basket: healthy meal from local grocery/supermarket.
    Returns { store, store_url, items, total_estimate }.
    Filters by allergies, intolerances, dislikes; favors favorites.
    """
    constraints = extract_dietary_constraints(patient)
    patient_name = patient.get("patient_name", "Unknown")

    # 1. Go to shop_feed, pick nearest grocery store (or use Continente Braga for braga-norte)
    stores_from_feed = scrape_grocery_stores_from_shop_feed(headless=headless)
    store_to_use: dict[str, Any] | None = None
    if city_slug and "braga" in city_slug.lower():
        store_to_use = next(
            (s for s in stores_from_feed if "continente" in (s.get("name") or "").lower()),
            None,
        )
    if not store_to_use and stores_from_feed:
        store_to_use = stores_from_feed[0]
    if not store_to_use:
        store_to_use = {"name": "Continente Bom Dia Braga", "url": CONTINENTE_BRAGA_URL}

    # 2. Get items: prefer all_menus (Continente Braga) for speed + real product URLs
    store_items: list[dict[str, Any]] = []
    store_url = store_to_use["url"].split("?")[0].rstrip("/")
    if "continente" in store_to_use["name"].lower():
        store_items = load_continente_grocery_from_all_menus(max_items=max_items_per_store * 2)
        for it in store_items:
            it["store_url"] = store_url
            it["restaurant_url"] = store_url
    if not store_items:
        store_items, store_url = scrape_store_healthy_products(
            store_url=store_to_use["url"],
            store_name=store_to_use["name"],
            headless=headless,
            max_items=max_items_per_store * 2,
        )

    if store_items:
        all_items_from_store = []
        for item in store_items:
            passes, _ = filter_menu_item(item, constraints)
            if passes:
                score = score_menu_item(item, constraints)
                all_items_from_store.append({
                    **item,
                    "restaurant": store_to_use["name"],
                    "restaurant_url": item.get("restaurant_url") or store_url,
                    "store_url": store_url,
                    "score": score,
                    "patient": patient_name,
                })
        if all_items_from_store:
            all_items_from_store.sort(key=lambda x: x["score"], reverse=True)
            basket = compose_healthy_basket(all_items_from_store, constraints, max_items=6)
            if not basket:
                basket = all_items_from_store[:6]
            if basket:
                enrich_items_parallel(basket)
                total_macros = {"protein": 0, "carbohydrate": 0, "fat": 0}
                for item in basket:
                    m = item.get("macronutrient_distribution_in_grams") or {}
                    total_macros["protein"] += m.get("protein", 0) or 0
                    total_macros["carbohydrate"] += m.get("carbohydrate", 0) or 0
                    total_macros["fat"] += m.get("fat", 0) or 0
                return {
                    "patient": patient_name,
                    "store": store_to_use["name"],
                    "store_url": store_url,
                    "items": basket,
                    "total_macros": total_macros,
                    "count": len(basket),
                }

    # 3. Fallback: shop_feed generic scrape
    shop_items, shop_url = scrape_shop_feed_healthy_items(
        constraints=constraints,
        headless=headless,
        max_items=max_items_per_store * 2,
    )
    if shop_items:
        all_items_from_shop = []
        for item in shop_items:
            passes, _ = filter_menu_item(item, constraints)
            if passes:
                score = score_menu_item(item, constraints)
                all_items_from_shop.append({
                    **item,
                    "restaurant": "Uber Eats Grocery",
                    "restaurant_url": item.get("restaurant_url") or shop_url,
                    "store_url": shop_url,
                    "score": score,
                    "patient": patient_name,
                })
        if all_items_from_shop:
            all_items_from_shop.sort(key=lambda x: x["score"], reverse=True)
            basket = compose_healthy_basket(all_items_from_shop, constraints, max_items=6)
            if not basket:
                basket = all_items_from_shop[:6]
            if basket:
                enrich_items_parallel(basket)
                total_macros = {"protein": 0, "carbohydrate": 0, "fat": 0}
                for item in basket:
                    m = item.get("macronutrient_distribution_in_grams") or {}
                    total_macros["protein"] += m.get("protein", 0) or 0
                    total_macros["carbohydrate"] += m.get("carbohydrate", 0) or 0
                    total_macros["fat"] += m.get("fat", 0) or 0
                return {
                    "patient": patient_name,
                    "store": "Uber Eats Grocery",
                    "store_url": shop_url,
                    "items": basket,
                    "total_macros": total_macros,
                    "count": len(basket),
                }

    # 4. Fallback: grocery category then healthy restaurants
    stores = scrape_grocery_stores(
        city_slug=city_slug,
        category="grocery",
        max_stores=max_stores,
        headless=headless,
    )
    if not stores:
        stores = scrape_healthy_restaurants(
            city_slug=city_slug,
            category="healthy",
            max_restaurants=max_stores,
            headless=headless,
        )

    all_items: list[dict[str, Any]] = []

    for store in stores:
        items = scrape_restaurant_menu(store["url"], headless=headless)
        for item in items[:max_items_per_store]:
            passes, _ = filter_menu_item(item, constraints)
            if passes:
                score = score_menu_item(item, constraints)
                all_items.append({
                    **item,
                    "restaurant": store["name"],
                    "restaurant_url": store["url"],
                    "score": score,
                    "patient": patient_name,
                })

    all_items.sort(key=lambda x: x["score"], reverse=True)

    # Compose basket from best store (we use items from first store that has enough)
    basket: list[dict[str, Any]] = []
    store_name = ""
    store_url = ""

    for store in stores:
        store_items = [i for i in all_items if i["restaurant"] == store["name"]]
        if len(store_items) >= 3:
            basket = compose_healthy_basket(store_items, constraints, max_items=6)
            if basket:
                store_name = store["name"]
                store_url = store["url"]
                break

    if not basket and all_items and stores:
        # Fallback: take top items from first store
        store_name = stores[0]["name"]
        store_url = stores[0]["url"]
        basket = all_items[:6]

    enrich_items_parallel(basket)

    total_macros = {"protein": 0, "carbohydrate": 0, "fat": 0}
    for item in basket:
        m = item.get("macronutrient_distribution_in_grams") or {}
        total_macros["protein"] += m.get("protein", 0) or 0
        total_macros["carbohydrate"] += m.get("carbohydrate", 0) or 0
        total_macros["fat"] += m.get("fat", 0) or 0

    return {
        "patient": patient_name,
        "store": store_name,
        "store_url": store_url,
        "items": basket,
        "total_macros": total_macros,
        "count": len(basket),
    }


def find_food_for_patient(
    patient: dict[str, Any],
    city_slug: str = "braga-norte",
    max_restaurants: int = 3,
    max_items_per_restaurant: int = 20,
    headless: bool = True,
    max_all_menus: int = 30,
) -> list[dict[str, Any]]:
    """
    Main pipeline: load patient → scrape healthy restaurants → scrape menus → filter by diet → score.
    Supplements with items from all_menus.json for more diverse selection.
    Returns ranked list of food items that fit the patient's dietary constraints.
    """
    constraints = extract_dietary_constraints(patient)
    patient_name = patient.get("patient_name", "Unknown")

    all_items: list[dict[str, Any]] = []

    # 1. Add items from all_menus.json (diverse pre-scraped menus)
    menu_items = load_all_menus_items()
    for item in menu_items[:max_all_menus * 2]:  # oversample, filter will reduce
        passes, _ = filter_menu_item(item, constraints)
        if passes:
            score = score_menu_item(item, constraints)
            all_items.append({
                **item,
                "restaurant": item.get("restaurant", "Uber Eats"),
                "restaurant_url": item.get("restaurant_url", ""),
                "score": score,
                "patient": patient_name,
            })

    # 2. Scrape live restaurants
    restaurants = scrape_healthy_restaurants(
        city_slug=city_slug,
        max_restaurants=max_restaurants,
        headless=headless,
    )
    for rest in restaurants:
        items = scrape_restaurant_menu(rest["url"], headless=headless)
        for item in items[:max_items_per_restaurant]:
            passes, _ = filter_menu_item(item, constraints)
            if passes:
                score = score_menu_item(item, constraints)
                all_items.append({
                    **item,
                    "restaurant": rest["name"],
                    "restaurant_url": rest["url"],
                    "score": score,
                    "patient": patient_name,
                })

    # Sort by score descending, dedupe by name+restaurant
    seen: set[tuple[str, str]] = set()
    unique: list[dict[str, Any]] = []
    for x in sorted(all_items, key=lambda i: i["score"], reverse=True):
        key = (x.get("name", "").strip().lower(), x.get("restaurant", "").strip().lower())
        if key in seen or len(key[0]) < 3:
            continue
        seen.add(key)
        unique.append(x)

    # Enrich top items with full nutrition (energy_kcal, macros) for instant display (parallel)
    enrich_items_parallel(unique[:20])

    return unique


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
