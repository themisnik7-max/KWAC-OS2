"""
KWAC OS — XP Engine
Pure Python. No AI. No external calls.
Calculates XP, goals hit, level, badges from raw submission data.
"""
from dataclasses import dataclass, field
from typing import Any
import math


# ── XP values per metric ─────────────────────────────────────
# Mirrors weekly_goals table. Source of truth is the DB,
# but we keep a local copy for fast in-process calculation.

XP_MAP = {
    "cold_calls":           {"xp": 1,   "target": 30, "bonus": 20},
    "social_media_leads":   {"xp": 3,   "target": 5,  "bonus": 15},
    "referrals":            {"xp": 5,   "target": 2,  "bonus": 10},
    "followup_calls":       {"xp": 2,   "target": 20, "bonus": 15},
    "first_meetings":       {"xp": 10,  "target": 4,  "bonus": 30},
    "second_meetings":      {"xp": 15,  "target": 2,  "bonus": 25},
    "exclusive_listings":   {"xp": 50,  "target": 1,  "bonus": 50},
    "simple_listings":      {"xp": 20,  "target": 2,  "bonus": 20},
    "sale_contracts":       {"xp": 100, "target": 1,  "bonus": 100},
    "rental_contracts":     {"xp": 60,  "target": 1,  "bonus": 60},
    "open_houses":          {"xp": 15,  "target": 1,  "bonus": 10},
    "trainings_attended":   {"xp": 10,  "target": 1,  "bonus": 0},
    "portal_leads":         {"xp": 2,   "target": 5,  "bonus": 10},
    "meetings_with_seller": {"xp": 8,   "target": 2,  "bonus": 15},
    "meetings_with_buyer":  {"xp": 8,   "target": 2,  "bonus": 15},
    "photo_shoots":         {"xp": 10,  "target": 1,  "bonus": 0},
    "new_partners":         {"xp": 15,  "target": 1,  "bonus": 10},
    "team_meetings":        {"xp": 5,   "target": 1,  "bonus": 0},
}

# Levels: XP thresholds
LEVELS = [0, 100, 300, 600, 1000, 1500, 2200, 3000, 4000, 5500, 7500]

# Badges: slug → (label, condition function receives lifetime stats dict)
BADGE_DEFINITIONS = [
    ("first_sale",      "Πρώτη πώληση",         lambda s: s.get("sale_contracts_total", 0) >= 1),
    ("cold_warrior",    "Cold Call Warrior",     lambda s: s.get("cold_calls_total", 0) >= 500),
    ("closer",          "The Closer",            lambda s: s.get("sale_contracts_total", 0) >= 10),
    ("listing_king",    "Listing King",          lambda s: s.get("exclusive_listings_total", 0) >= 20),
    ("streak_4",        "Σταθερός 4 εβδομάδες",  lambda s: s.get("streak_weeks", 0) >= 4),
    ("streak_12",       "Ακατανίκητος",          lambda s: s.get("streak_weeks", 0) >= 12),
    ("open_house_pro",  "Open House Pro",        lambda s: s.get("open_houses_total", 0) >= 10),
    ("networker",       "Super Networker",       lambda s: s.get("new_partners_total", 0) >= 20),
]


@dataclass
class XPResult:
    xp_earned: int = 0
    goals_hit: dict = field(default_factory=dict)
    goals_summary: dict = field(default_factory=dict)   # metric → {value, target, hit, xp}
    new_badges: list = field(default_factory=list)
    level_before: int = 1
    level_after: int = 1
    leveled_up: bool = False


def calculate_xp(submission: dict[str, Any]) -> XPResult:
    """
    Given a submission dict (column_name → value),
    returns XPResult with total XP, goals hit, and bonus info.
    No DB calls — pure computation.
    """
    result = XPResult()
    total_xp = 0

    for metric, cfg in XP_MAP.items():
        value = submission.get(metric, 0) or 0
        target = cfg["target"]
        xp_per_unit = cfg["xp"]
        bonus = cfg["bonus"]

        earned = value * xp_per_unit
        hit = value >= target
        if hit:
            earned += bonus

        total_xp += earned
        result.goals_hit[metric] = hit
        result.goals_summary[metric] = {
            "value": value,
            "target": target,
            "hit": hit,
            "xp": earned,
        }

    result.xp_earned = total_xp
    return result


def compute_level(xp_total: int) -> int:
    """Returns the level (1-based) for a given total XP."""
    for i, threshold in enumerate(reversed(LEVELS)):
        if xp_total >= threshold:
            return len(LEVELS) - i
    return 1


def compute_new_badges(existing_badges: list[str], lifetime_stats: dict) -> list[str]:
    """
    Returns list of newly earned badge slugs (not already held).
    lifetime_stats should contain totals like sale_contracts_total, streak_weeks, etc.
    """
    new = []
    for slug, label, condition in BADGE_DEFINITIONS:
        if slug not in existing_badges and condition(lifetime_stats):
            new.append(slug)
    return new


def weekly_winners(submissions: list[dict]) -> dict[str, dict]:
    """
    Given a list of submission dicts for the same week,
    returns the winner per category.
    Returns: {"cold_calls": {"agent_id": ..., "value": ..., "name": ...}, ...}
    """
    categories = {
        "cold_calls":       "King of Cold Calls",
        "first_meetings":   "Meeting Machine",
        "exclusive_listings": "Listing Master",
        "sale_contracts":   "Deal Closer",
        "open_houses":      "Open House Champion",
        "followup_calls":   "Follow Up King",
    }

    winners = {}
    for metric, title in categories.items():
        best = None
        best_val = -1
        for sub in submissions:
            val = sub.get(metric, 0) or 0
            if val > best_val:
                best_val = val
                best = sub
        if best and best_val > 0:
            winners[metric] = {
                "title": title,
                "agent_id": best.get("agent_id"),
                "agent_name": best.get("full_name", ""),
                "value": best_val,
                "metric": metric,
            }

    return winners


def leaderboard(agents_with_submissions: list[dict]) -> list[dict]:
    """
    Ranks agents by XP this week.
    Input: list of dicts with agent info + weekly_xp field.
    Returns sorted list with rank added.
    """
    ranked = sorted(
        agents_with_submissions,
        key=lambda a: a.get("xp_this_week", 0),
        reverse=True,
    )
    for i, agent in enumerate(ranked):
        agent["rank"] = i + 1
        agent["rank_delta"] = agent.get("rank_last_week", i + 1) - (i + 1)
    return ranked
