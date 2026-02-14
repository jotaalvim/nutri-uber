import json
import os
import time

# Configuration
MENU_FILE = 'all_menus.json'
PATIENT_FILE = 'nutri-approval.json'
CACHE_FILE = 'nutrition_cache.json'
INGREDIENT_CACHE_FILE = 'ingredient_cache.json'
OUTPUT_FILE = 'matches.json'
FOOD_DB_FILE = 'food_database.json'
# LIMIT_ITEMS = 50 # Removed limit to process all items 

def load_json(filepath):
    """Loads a JSON file."""
    try:
        with open(filepath, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        # Return appropriate empty type
        return {} if 'cache' in filepath else []

def save_json(data, filepath):
    """Saves data to a JSON file."""
    with open(filepath, 'w') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def load_local_db():
    """Loads the local food database."""
    try:
         with open(FOOD_DB_FILE, 'r') as f:
            data = json.load(f)
            return data.get('foods', [])
    except FileNotFoundError:
        print("Warning: food_database.json not found.")
        return []

def find_in_local_db(query, db):
    """Searches for a query match in the local database keywords."""
    query_lower = query.lower()
    
    # Direct match first (exact)
    for food in db:
        if query_lower == food['name'].lower():
             return food
    
    # Keyword match (contains)
    for food in db:
        for keyword in food.get('keywords', []):
            if keyword in query_lower:
                return food
    return None


def extract_ingredients(name):
    """Splits menu item into potential individual ingredients."""
    import re
    # Lowercase for consistent splitting
    cleaned = name.lower()
    
    # Remove numbers like "1. ", "70. " at start
    cleaned = re.sub(r'^\d+\.?\s*', '', cleaned)
    # Remove parens like "(7 uni)" and contents
    cleaned = re.sub(r'\s*\(.*?\)', '', cleaned)
    
    # Remove typical irrelevant words (adj, marketing)
    stop_words = ["delicioso", "caseiro", "fresco", "tradicional", "da casa", "especial", "grelhado", "frito", "assado", "no forno", "molho", "com"]
    # Be careful with "molho" (sauce) - sometimes it's the main flavor, but "com molho" usually implies the sauce is secondary.
    # For now, we strip "com" via regex below, but words like "caseiro" are noise.
    
    for word in stop_words:
        cleaned = re.sub(r'\b' + word + r'\b', '', cleaned, flags=re.IGNORECASE)

    # Split by delimiters: comma, ' e ', ' com ', ' + ', ' ou '
    parts = re.split(r',|\s+(?:e|com|plus|\+|&)\s+', cleaned)
    
    # Final cleanup of each part
    results = []
    for p in parts:
        p = p.strip()
        # Remove empty or very short strings (e.g., after removing stopwords)
        if len(p) > 2:
            results.append(p)
            
    return results

def get_nutrition_from_local_db(original_name, db):
    """Parses menu name, searches local DB, aggregates nutrition."""
    ingredients = extract_ingredients(original_name)
    if not ingredients:
        # Fallback: try whole string match if extraction failed to produce tokens
        ingredients = [original_name]

    print(f"  Analysing item: '{original_name}' -> {ingredients}")
    
    results = []
    for ing in ingredients:
        food_data = find_in_local_db(ing, db)
        if food_data:
            print(f"    -> Local DB Match: '{ing}' -> {food_data['name']}")
            results.append(food_data)
        else:
            print(f"    -> No match for '{ing}'")
            
    if not results:
        return None
        
    avg_nutrition = {}
    keys = ['energy_kcal_100g', 'proteins_100g', 'fat_100g', 'carbohydrates_100g', 'fiber_100g']
    
    for k in keys:
        valid_values = [r.get(k, 0) for r in results]
        if valid_values:
            avg_nutrition[k] = round(sum(valid_values) / len(valid_values), 2)
        else:
            avg_nutrition[k] = 0
            
    avg_nutrition['product_name'] = ' + '.join([r['name'] for r in results])
    return avg_nutrition

def main():
    print("Loading data...")
    menus = load_json(MENU_FILE)
    patients = load_json(PATIENT_FILE)
    local_db = load_local_db()
    
    print(f"Loaded {len(local_db)} items from local food database.")

    unique_items_map = {} 

    # Collect unique items to process
    count = 0
    print("Extracting menu items...")
    if isinstance(menus, list):
        for store in menus:
            if 'menu' not in store: continue
            for item in store['menu']:
                name = item.get('name')
                if name and name not in unique_items_map:
                    # Filter out likely non-food items
                    if name.lower() in ['featured items', '#1 most liked', 'popular items', 'combos']: continue
                    if name.startswith('#') and 'most liked' in name: continue
                    
                    unique_items_map[name] = item
                    # count += 1 # limit removed
            # if count >= LIMIT_ITEMS: break # limit removed
    
    print(f"Processing {len(unique_items_map)} unique items from menus...")

    # Process items using local DB
    item_nutrition_map = {}
    
    for name in unique_items_map:
        nutrition = get_nutrition_from_local_db(name, local_db)
        if nutrition:
            item_nutrition_map[name] = nutrition
        # No caching needed for local DB lookups as they are instant

    # Match with patients
    matches = []
    print("Matching items to patient needs...")
    
    for patient in patients:
        p_name = patient.get('patient_name', 'Unknown')
        p_macros = patient.get('macronutrient_distribution_in_grams', {})
        
        # Calculate patient macro ratios
        p_fat = p_macros.get('fat', 0)
        p_carb = p_macros.get('carbohydrate', 0)
        p_prot = p_macros.get('protein', 0)
        
        total_g = p_fat + p_carb + p_prot
        if total_g == 0: continue
        
        target_ratios = {
            'fat': p_fat / total_g,
            'carb': p_carb / total_g,
            'protein': p_prot / total_g
        }
        
        patient_matches = []
        for name, item in unique_items_map.items():
            nutri = item_nutrition_map.get(name)
            if not nutri: continue
            
            n_fat = nutri.get('fat_100g', 0)
            n_carb = nutri.get('carbohydrates_100g', 0)
            n_prot = nutri.get('proteins_100g', 0)
            
            n_total = n_fat + n_carb + n_prot
            if n_total == 0: continue
            
            item_ratios = {
                'fat': n_fat / n_total,
                'carb': n_carb / n_total,
                'protein': n_prot / n_total
            }
            
            # Simple similarity score
            diff = (
                (target_ratios['fat'] - item_ratios['fat'])**2 +
                (target_ratios['carb'] - item_ratios['carb'])**2 +
                (target_ratios['protein'] - item_ratios['protein'])**2
            )
            
            patient_matches.append({
                'item_name': name,
                'price': item.get('price'),
                'nutrition_per_100g': {k:v for k,v in nutri.items() if k.endswith('_100g')},
                'macros_ratio': {k: round(v, 2) for k,v in item_ratios.items()},
                'fit_score': round(diff, 4),
                'matched_product_name': nutri.get('product_name') 
            })
        
        # Sort matches by best fit (lowest diff)
        patient_matches.sort(key=lambda x: x['fit_score'])
        
        matches.append({
            'patient': p_name,
            'target_ratios': {k: round(v, 2) for k,v in target_ratios.items()},
            'top_matches': patient_matches[:5] 
        })
        
    save_json(matches, OUTPUT_FILE)
    print(f"Created {len(matches)} patient match lists.")
    print(f"Results saved to {OUTPUT_FILE}")

if __name__ == "__main__":
    main()
