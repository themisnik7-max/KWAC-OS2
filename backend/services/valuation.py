"""
KWAC OS — Property Valuation Engine
Pure Python + scikit-learn. Claude is called ONLY for the reasoning paragraph
at the very end, after statistics are computed.

Method: Weighted K-Nearest Neighbors on geographic + physical features.
Location is weighted 3x more than physical features (as requested).
"""
import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import Optional
from geopy.distance import geodesic


# ── Feature weights ──────────────────────────────────────────
# Location is 3x more important than physical features.
# These multiply the normalized feature values before KNN distance calc.

WEIGHTS = {
    "dist_km":          3.0,   # geographic distance (computed, not a column)
    "sqm":              1.0,
    "floor":            0.5,
    "year_built":       0.8,
    "year_renovated":   0.6,
    "condition_score":  0.9,   # new=5, excellent=4, good=3, fair=2, needs_work=1
}

CONDITION_SCORE = {
    "new": 5, "excellent": 4, "good": 3, "fair": 2, "needs_work": 1, None: 3
}

MIN_COMPARABLES = 3    # refuse to estimate with fewer than this
MAX_COMPARABLES = 15   # cap to avoid outlier noise
MAX_DISTANCE_KM = 5.0  # only use properties within 5km


@dataclass
class ValuationResult:
    price_min: float
    price_max: float
    price_median: float
    price_per_sqm: float
    comparables_count: int
    comparables_ids: list[str]
    confidence: str          # "high" | "medium" | "low"
    method_notes: str        # human-readable explanation of what we did
    error: Optional[str] = None


def _condition_score(condition: Optional[str]) -> int:
    return CONDITION_SCORE.get(condition, 3)


def run_valuation(
    input_lat: float,
    input_lng: float,
    input_sqm: float,
    input_floor: Optional[int],
    input_year_built: Optional[int],
    input_year_renovated: Optional[int],
    input_condition: Optional[str],
    input_transaction: str,          # "sale" or "rental"
    input_type: Optional[str],       # "apartment", "house", etc.
    sold_properties: list[dict],     # fetched from DB: only closed deals with final_price
) -> ValuationResult:
    """
    sold_properties: list of dicts with keys:
        id, lat, lng, sqm, floor, year_built, year_renovated,
        condition, price_final, transaction_type, property_type
    """

    if not sold_properties:
        return ValuationResult(
            price_min=0, price_max=0, price_median=0,
            price_per_sqm=0, comparables_count=0, comparables_ids=[],
            confidence="low", method_notes="",
            error="No sold properties in database yet. Add more sales data to improve accuracy."
        )

    # Filter to same transaction type and property type
    df = pd.DataFrame(sold_properties)
    df = df[df["transaction_type"] == input_transaction]
    if input_type:
        df = df[df["property_type"] == input_type]

    if len(df) < MIN_COMPARABLES:
        return ValuationResult(
            price_min=0, price_max=0, price_median=0,
            price_per_sqm=0, comparables_count=0, comparables_ids=[],
            confidence="low", method_notes="",
            error=f"Not enough comparable {input_transaction}s in the database ({len(df)} found, need {MIN_COMPARABLES})."
        )

    # Step 1: Compute geographic distance for each comparable
    input_coords = (input_lat, input_lng)
    df["dist_km"] = df.apply(
        lambda row: geodesic(input_coords, (row["lat"], row["lng"])).km
        if pd.notna(row["lat"]) and pd.notna(row["lng"]) else 999,
        axis=1
    )

    # Filter to MAX_DISTANCE_KM radius
    df_nearby = df[df["dist_km"] <= MAX_DISTANCE_KM].copy()

    if len(df_nearby) < MIN_COMPARABLES:
        # Fallback: relax to nearest 10 regardless of distance
        df_nearby = df.nsmallest(10, "dist_km").copy()
        distance_note = f"No properties within {MAX_DISTANCE_KM}km — using nearest {len(df_nearby)} in wider area."
    else:
        distance_note = f"{len(df_nearby)} properties within {MAX_DISTANCE_KM}km."

    # Step 2: Score each comparable using weighted features
    df_nearby["condition_score"] = df_nearby["condition"].apply(_condition_score)
    input_cond_score = _condition_score(input_condition)

    def weighted_distance(row):
        score = 0.0
        score += WEIGHTS["dist_km"] * row["dist_km"]
        if input_sqm and pd.notna(row["sqm"]) and row["sqm"] > 0:
            score += WEIGHTS["sqm"] * abs(input_sqm - row["sqm"]) / max(input_sqm, 1)
        if input_floor is not None and pd.notna(row["floor"]):
            score += WEIGHTS["floor"] * abs(input_floor - row["floor"]) / 10
        if input_year_built and pd.notna(row["year_built"]):
            score += WEIGHTS["year_built"] * abs(input_year_built - row["year_built"]) / 50
        if input_year_renovated and pd.notna(row["year_renovated"]):
            score += WEIGHTS["year_renovated"] * abs(input_year_renovated - row["year_renovated"]) / 30
        score += WEIGHTS["condition_score"] * abs(input_cond_score - row["condition_score"]) / 4
        return score

    df_nearby["similarity_score"] = df_nearby.apply(weighted_distance, axis=1)

    # Step 3: Take the best K comparables
    k = min(MAX_COMPARABLES, len(df_nearby))
    top = df_nearby.nsmallest(k, "similarity_score")

    # Step 4: Compute price per sqm for each comparable, then estimate
    top = top.copy()
    top["price_per_sqm"] = top.apply(
        lambda r: r["price_final"] / r["sqm"] if pd.notna(r["sqm"]) and r["sqm"] > 0 else np.nan,
        axis=1
    )
    top = top.dropna(subset=["price_per_sqm"])

    if len(top) < MIN_COMPARABLES:
        return ValuationResult(
            price_min=0, price_max=0, price_median=0,
            price_per_sqm=0, comparables_count=0, comparables_ids=[],
            confidence="low", method_notes="",
            error="Comparable properties are missing sqm data. Update property records."
        )

    # Weighted median price per sqm (closer = more weight)
    # Invert similarity score for weights (lower score = higher weight)
    max_score = top["similarity_score"].max() + 0.001
    top["weight"] = max_score - top["similarity_score"]
    total_weight = top["weight"].sum()
    weighted_ppsqm = (top["price_per_sqm"] * top["weight"]).sum() / total_weight

    # Estimate total price
    estimated_price = weighted_ppsqm * input_sqm if input_sqm else None

    # Build range: ±15% as price band
    band = 0.15
    price_min = round(estimated_price * (1 - band), -3) if estimated_price else 0
    price_max = round(estimated_price * (1 + band), -3) if estimated_price else 0
    price_median = round(estimated_price, -3) if estimated_price else 0

    # Confidence based on number of nearby comparables and distance spread
    avg_dist = top["dist_km"].mean()
    if len(top) >= 8 and avg_dist < 1.5:
        confidence = "high"
    elif len(top) >= 4 and avg_dist < 3.0:
        confidence = "medium"
    else:
        confidence = "low"

    method_notes = (
        f"Used {len(top)} comparable {input_transaction}s. "
        f"Average distance: {avg_dist:.1f}km. "
        f"Weighted €/sqm: {weighted_ppsqm:.0f}. "
        f"{distance_note}"
    )

    return ValuationResult(
        price_min=price_min,
        price_max=price_max,
        price_median=price_median,
        price_per_sqm=round(weighted_ppsqm, 2),
        comparables_count=len(top),
        comparables_ids=[str(r) for r in top["id"].tolist()],
        confidence=confidence,
        method_notes=method_notes,
    )
