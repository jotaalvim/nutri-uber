"""
File-based cache for food finder results.
Reduces wait time for repeat requests (cache hit = instant).
"""

import hashlib
import json
import time
from pathlib import Path

CACHE_DIR = Path(__file__).parent / ".cache" / "food_finder"
TTL_HOURS = 6


def _cache_key(patient_id: str | int | None, city: str, suffix: str = "") -> str:
    """Generate cache key from patient_id, city, and optional suffix."""
    key = f"{patient_id or 'unknown'}_{city}{suffix}"
    return hashlib.sha256(key.encode()).hexdigest()[:32]


def get(patient_id: str | int | None, city: str, suffix: str = "") -> dict | None:
    """Return cached result if valid, else None."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"{_cache_key(patient_id, city, suffix)}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        cached_at = data.get("cached_at", 0)
        if time.time() - cached_at > TTL_HOURS * 3600:
            path.unlink(missing_ok=True)
            return None
        return data
    except Exception:
        path.unlink(missing_ok=True)
        return None


def set(patient_id: str | int | None, city: str, patient_name: str, items: list, suffix: str = "") -> None:
    """Store result in cache."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"{_cache_key(patient_id, city, suffix)}.json"
    data = {
        "patient": patient_name,
        "count": len(items),
        "items": items,
        "cached_at": time.time(),
        "from_cache": True,
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=0), encoding="utf-8")


def get_grocery(patient_id: str | int | None, city: str) -> dict | None:
    """Return cached grocery basket if valid, else None."""
    return get(patient_id, city, suffix="_grocery")


def set_grocery(patient_id: str | int | None, city: str, data: dict) -> None:
    """Store grocery basket in cache."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"{_cache_key(patient_id, city, '_grocery')}.json"
    data = {**data, "cached_at": time.time(), "from_cache": True}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=0), encoding="utf-8")


def list_grocery_baskets() -> list[dict]:
    """List all cached grocery baskets. Returns summary of each basket."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    baskets = []
    for path in CACHE_DIR.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if "store" not in data or "store_url" not in data:
                continue  # Not a grocery basket
            cached_at = data.get("cached_at", 0)
            if time.time() - cached_at > TTL_HOURS * 3600:
                continue  # Expired
            baskets.append({
                "patient": data.get("patient", "Unknown"),
                "store": data.get("store", ""),
                "store_url": data.get("store_url", ""),
                "count": data.get("count", 0),
                "total_macros": data.get("total_macros", {}),
                "items": data.get("items", []),
            })
        except Exception:
            continue
    return baskets
