"""
Deterministic tool functions for the recommendation pipeline.
No LLM calls here — these are pure Python calculations.
"""
import json
from typing import Optional
from backend.config import DATA_DIR, PACKING_EFFICIENCY, WEIGHT_SAFETY_MARGIN


def _load(filename: str) -> dict:
    with open(DATA_DIR / filename, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Object lookup
# ---------------------------------------------------------------------------

def lookup_object(name: str) -> Optional[dict]:
    """
    Find an item in the common_objects database by name or alias.
    Returns {"key", "weight_lb", "volume_cuft"} or None.
    Normalises underscores ↔ spaces so "sofa 3 seat" matches key "sofa_3_seat".
    """
    obj_data = _load("common_objects.json")
    needle = name.lower().strip().replace("_", " ")

    def norm(s: str) -> str:
        return s.lower().replace("_", " ")

    for cat in obj_data["categories"].values():
        for key, item in cat["items"].items():
            key_n = norm(key)
            if needle == key_n or needle in key_n or key_n in needle:
                return {"key": key, "weight_lb": item["weight_lb"], "volume_cuft": item["volume_cuft"]}
            for alias in item.get("aliases", []):
                alias_n = norm(alias)
                if needle in alias_n or alias_n in needle:
                    return {"key": key, "weight_lb": item["weight_lb"], "volume_cuft": item["volume_cuft"]}
    return None


def web_search_object(name: str) -> Optional[dict]:
    """
    Fallback: search DuckDuckGo for weight/dimension info on unknown items.
    Returns a dict with raw text snippets the LLM can interpret.
    """
    try:
        import requests
        from bs4 import BeautifulSoup

        query = f"{name} weight pounds dimensions average"
        url = f"https://html.duckduckgo.com/html/?q={query.replace(' ', '+')}"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        resp = requests.get(url, headers=headers, timeout=6)
        soup = BeautifulSoup(resp.text, "html.parser")
        snippets = [el.get_text(strip=True) for el in soup.select(".result__snippet")][:3]
        return {"source": "web_search", "query": query, "snippets": snippets}
    except Exception as e:
        return {"source": "web_search", "error": str(e), "snippets": []}


# ---------------------------------------------------------------------------
# Load calculation
# ---------------------------------------------------------------------------

def calculate_load(items: list[dict]) -> dict:
    """
    Compute total cargo weight and volume, applying safety margins.

    Each item dict: {"name": str, "quantity": int, "weight_lb": float|None, "volume_cuft": float|None}

    Returns a summary with raw totals, safety-adjusted requirements, and any
    items that could not be resolved from the database.
    """
    total_weight = 0.0
    total_volume = 0.0
    unresolved: list[str] = []
    resolved_items: list[dict] = []

    for item in items:
        name = item.get("name", "unknown")
        qty = max(int(item.get("quantity", 1)), 1)
        weight = item.get("weight_lb")
        volume = item.get("volume_cuft")

        if weight is None or volume is None:
            db = lookup_object(name)
            if db:
                weight = weight if weight is not None else db["weight_lb"]
                volume = volume if volume is not None else db["volume_cuft"]
            else:
                unresolved.append(name)
                weight = weight if weight is not None else 30.0   # conservative unknown default
                volume = volume if volume is not None else 5.0

        total_weight += weight * qty
        total_volume += volume * qty
        resolved_items.append({
            "name": name,
            "quantity": qty,
            "unit_weight_lb": round(weight, 1),
            "unit_volume_cuft": round(volume, 1),
            "subtotal_weight_lb": round(weight * qty, 1),
            "subtotal_volume_cuft": round(volume * qty, 1),
        })

    required_payload = total_weight * WEIGHT_SAFETY_MARGIN
    required_volume = total_volume / PACKING_EFFICIENCY

    return {
        "items": resolved_items,
        "raw_weight_lb": round(total_weight, 1),
        "raw_volume_cuft": round(total_volume, 1),
        "required_payload_lb": round(required_payload, 1),   # with 12% safety buffer
        "required_volume_cuft": round(required_volume, 1),   # accounting for 75% packing efficiency
        "unresolved_items": unresolved,
    }


# ---------------------------------------------------------------------------
# Truck filtering and ranking
# ---------------------------------------------------------------------------

def _parse_space(val) -> float:
    """Handle integer or string-with-plus loading space values."""
    if isinstance(val, (int, float)):
        return float(val)
    return float(str(val).replace("+", "").strip())


def filter_trucks(required_weight_lb: float, required_volume_cuft: float, has_cdl: bool) -> list[dict]:
    """
    Return trucks from the fleet that satisfy weight, volume, and CDL constraints.
    Always returns a list (may be empty).
    """
    trucks = _load("truck_data.json")["trucks"]
    compatible = []
    for truck in trucks:
        if truck["cdl_required"] and not has_cdl:
            continue
        if truck["max_payload_lb"] < required_weight_lb:
            continue
        if _parse_space(truck["max_loading_space_cuft"]) < required_volume_cuft:
            continue
        compatible.append(truck)
    return compatible


def estimate_cost(truck_id: str, days: int, miles: float) -> dict:
    """
    Return a full cost breakdown for a given truck, duration, and mileage.
    Includes base rate, mileage, environmental fee, and fuel estimate.
    Insurance NOT included (user selects separately).
    """
    cost_data = _load("cost_profiles.json")
    rates = cost_data["base_rates"].get(truck_id)
    if not rates:
        return {}

    fees = cost_data["additional_fees"]
    truck_data = _load("truck_data.json")
    truck = next((t for t in truck_data["trucks"] if t["id"] == truck_id), None)

    base = rates["daily_rate_usd"] * days
    mileage_cost = rates["per_mile_rate_usd"] * miles
    env_fee = fees["environmental_fee_per_day_usd"] * days

    fuel_cost = 0.0
    if truck:
        diesel_price = cost_data["fuel_cost_reference"]["diesel_price_per_gal_usd"]
        fuel_cost = (miles / truck["max_mpg"]) * diesel_price

    subtotal = base + mileage_cost + env_fee
    total_with_fuel = subtotal + fuel_cost

    return {
        "truck_id": truck_id,
        "days": days,
        "miles": miles,
        "base_rate_usd": round(base, 2),
        "mileage_cost_usd": round(mileage_cost, 2),
        "fuel_estimate_usd": round(fuel_cost, 2),
        "environmental_fee_usd": round(env_fee, 2),
        "subtotal_before_fuel_usd": round(subtotal, 2),
        "estimated_total_usd": round(total_with_fuel, 2),
        "insurance_note": "Add $14–$29/day for protection plans (not included above).",
    }


def rank_trucks(
    compatible_trucks: list[dict],
    days: int,
    miles: float,
    required_weight_lb: float,
    required_volume_cuft: float,
    priority: str = "cost",
) -> list[dict]:
    """
    Attach cost estimates and utilization metrics to each truck, then sort.
    priority: "cost" | "space" | "fuel_efficiency"
    """
    ranked = []
    for truck in compatible_trucks:
        cost = estimate_cost(truck["id"], days, miles)
        space = _parse_space(truck["max_loading_space_cuft"])

        payload_util = (required_weight_lb / truck["max_payload_lb"]) * 100
        volume_util = (required_volume_cuft / space) * 100 if space > 0 else 0

        ranked.append({
            **truck,
            "cost_estimate": cost,
            "payload_utilization_pct": round(payload_util, 1),
            "volume_utilization_pct": round(volume_util, 1),
            "estimated_total_usd": cost.get("estimated_total_usd", 0),
        })

    if priority == "cost":
        ranked.sort(key=lambda t: t["estimated_total_usd"])
    elif priority == "space":
        # Prefer truck with most headroom (lowest utilization = most space for the load)
        ranked.sort(key=lambda t: t["volume_utilization_pct"])
    elif priority == "fuel_efficiency":
        ranked.sort(key=lambda t: -t["max_mpg"])

    return ranked
