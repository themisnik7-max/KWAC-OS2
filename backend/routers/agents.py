"""
KWAC OS -- Agents Router
Handles: weekly submissions, sprint calls, GPS goals, leaderboard, agent profile
"""
import json
from datetime import date, timedelta, datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from database import get_db
from auth import require_role

router = APIRouter()

METRIC_KEYS = [
    "cold_calls", "social_media_leads", "mail_leads", "portal_leads", "referrals",
    "followup_calls", "first_meetings", "second_meetings", "meetings_with_seller",
    "meetings_with_buyer", "meetings_with_tenant", "exclusive_listings", "simple_listings",
    "sale_contracts", "purchase_contracts", "rental_contracts", "photo_shoots", "open_houses",
    "matterport_scans", "floor_plans", "new_partners", "referrals_given",
    "trainings_attended", "team_meetings", "conferences",
]


def week_bounds(ref: date = None) -> tuple[date, date]:
    d = ref or date.today()
    monday = d - timedelta(days=d.weekday())
    return monday, monday + timedelta(days=6)


@router.get("/me")
async def get_me(
    user=Depends(require_role("agent", "ceo", "admin")),
    db: AsyncSession = Depends(get_db),
):
    r = await db.execute(
        text("""
            SELECT xp_total, xp_this_week, xp_this_month, level,
                   streak_weeks, streak_best, team, phone, last_submitted
            FROM agents WHERE id = :uid
        """),
        {"uid": user["id"]},
    )
    row = r.mappings().first()
    base = {
        "id": str(user["id"]),
        "email": user["email"],
        "full_name": user["full_name"],
        "role": user["role"],
    }
    if row:
        base.update({k: v for k, v in dict(row).items() if v is not None})
    return base


@router.get("/goals")
async def get_goals(
    user=Depends(require_role("agent", "ceo", "admin")),
    db: AsyncSession = Depends(get_db),
):
    r = await db.execute(text("""
        SELECT metric, target, xp_value, xp_bonus, label_el, category, sort_order
        FROM weekly_goals WHERE is_active = TRUE ORDER BY sort_order
    """))
    return [dict(row) for row in r.mappings()]


@router.get("/weekly/current")
async def get_current_week(
    user=Depends(require_role("agent", "ceo", "admin")),
    db: AsyncSession = Depends(get_db),
):
    monday, _ = week_bounds()
    r = await db.execute(
        text("SELECT * FROM weekly_submissions WHERE agent_id = :uid AND week_start = :ws"),
        {"uid": user["id"], "ws": monday},
    )
    row = r.mappings().first()
    if not row:
        return None
    d = dict(row)
    d["id"] = str(d["id"])
    d["agent_id"] = str(d["agent_id"])
    if d.get("submitted_at"):
        d["submitted_at"] = d["submitted_at"].isoformat()
    return d


class WeeklySubmit(BaseModel):
    cold_calls: int = 0
    social_media_leads: int = 0
    mail_leads: int = 0
    portal_leads: int = 0
    referrals: int = 0
    followup_calls: int = 0
    first_meetings: int = 0
    second_meetings: int = 0
    meetings_with_seller: int = 0
    meetings_with_buyer: int = 0
    meetings_with_tenant: int = 0
    exclusive_listings: int = 0
    simple_listings: int = 0
    sale_contracts: int = 0
    purchase_contracts: int = 0
    rental_contracts: int = 0
    photo_shoots: int = 0
    open_houses: int = 0
    matterport_scans: int = 0
    floor_plans: int = 0
    new_partners: int = 0
    referrals_given: int = 0
    trainings_attended: int = 0
    team_meetings: int = 0
    conferences: int = 0
    notes: Optional[str] = None


async def _calc_xp(data: dict, db: AsyncSession) -> tuple[int, dict]:
    r = await db.execute(text(
        "SELECT metric, target, xp_value, xp_bonus FROM weekly_goals WHERE is_active = TRUE"
    ))
    goals = {row["metric"]: dict(row) for row in r.mappings()}
    xp = 0
    goals_hit = {}
    for metric, goal in goals.items():
        val = data.get(metric, 0) or 0
        xp += val * goal["xp_value"]
        hit = val >= goal["target"]
        goals_hit[metric] = hit
        if hit and goal["xp_bonus"] > 0:
            xp += goal["xp_bonus"]
    return max(0, xp), goals_hit


@router.post("/weekly/submit")
async def submit_weekly(
    body: WeeklySubmit,
    user=Depends(require_role("agent", "ceo", "admin")),
    db: AsyncSession = Depends(get_db),
):
    monday, sunday = week_bounds()
    data = body.model_dump()
    xp, goals_hit = await _calc_xp(data, db)

    cols = ", ".join(METRIC_KEYS)
    vals = ", ".join(f":{k}" for k in METRIC_KEYS)
    upds = ", ".join(f"{k} = :{k}" for k in METRIC_KEYS)

    params = {k: data.get(k, 0) for k in METRIC_KEYS}
    params.update({
        "uid": user["id"], "ws": monday, "we": sunday,
        "xp": xp, "goals": json.dumps(goals_hit), "notes": data.get("notes"),
    })

    await db.execute(text(f"""
        INSERT INTO weekly_submissions
            (agent_id, week_start, week_end, {cols}, xp_earned, goals_hit, notes, submitted_at)
        VALUES (:uid, :ws, :we, {vals}, :xp, :goals::jsonb, :notes, NOW())
        ON CONFLICT (agent_id, week_start) DO UPDATE SET
            {upds},
            xp_earned   = :xp,
            goals_hit   = :goals::jsonb,
            notes       = :notes,
            submitted_at = NOW(),
            updated_at  = NOW()
    """), params)

    await db.execute(text("""
        INSERT INTO agents (id, xp_total, xp_this_week, last_submitted)
        VALUES (
            :uid,
            (SELECT COALESCE(SUM(xp_earned),0) FROM weekly_submissions WHERE agent_id = :uid),
            :xp,
            :ws
        )
        ON CONFLICT (id) DO UPDATE SET
            xp_total     = (SELECT COALESCE(SUM(xp_earned),0) FROM weekly_submissions WHERE agent_id = :uid),
            xp_this_week = :xp,
            last_submitted = :ws,
            updated_at   = NOW()
    """), {"uid": user["id"], "xp": xp, "ws": monday})

    await db.commit()
    return {"ok": True, "xp_earned": xp, "week_start": str(monday)}


@router.get("/leaderboard")
async def leaderboard(
    user=Depends(require_role("agent", "ceo", "admin")),
    db: AsyncSession = Depends(get_db),
):
    monday, _ = week_bounds()
    r = await db.execute(text("""
        SELECT
            u.id, u.full_name, u.email,
            COALESCE(a.xp_total, 0)      AS xp_total,
            COALESCE(a.level, 1)          AS level,
            COALESCE(a.streak_weeks, 0)   AS streak_weeks,
            COALESCE(ws.xp_earned, 0)     AS xp_this_week,
            ws.submitted_at
        FROM users u
        LEFT JOIN agents a ON a.id = u.id
        LEFT JOIN weekly_submissions ws
               ON ws.agent_id = u.id AND ws.week_start = :monday
        WHERE u.is_active = TRUE AND u.role IN ('agent', 'ceo')
        ORDER BY COALESCE(ws.xp_earned, 0) DESC, COALESCE(a.xp_total, 0) DESC
    """), {"monday": monday})
    rows = r.mappings().all()
    return [
        {
            "id": str(row["id"]),
            "full_name": row["full_name"],
            "xp_this_week": row["xp_this_week"],
            "xp_total": row["xp_total"],
            "level": row["level"],
            "streak_weeks": row["streak_weeks"],
            "submitted_at": row["submitted_at"].isoformat() if row["submitted_at"] else None,
        }
        for row in rows
    ]


class SprintLog(BaseModel):
    session_date: date
    session_number: int
    calls_made: int = 0
    leads_generated: int = 0
    meetings_booked: int = 0


@router.get("/sprint")
async def get_sprint(
    user=Depends(require_role("agent", "ceo", "admin")),
    db: AsyncSession = Depends(get_db),
):
    monday, sunday = week_bounds()
    r = await db.execute(text("""
        SELECT
            ss.id, ss.session_date, ss.session_number, ss.notes,
            COALESCE(se.calls_made, 0)      AS calls_made,
            COALESCE(se.leads_generated, 0) AS leads_generated,
            COALESCE(se.meetings_booked, 0) AS meetings_booked,
            se.entered_at
        FROM sprint_sessions ss
        LEFT JOIN sprint_entries se
               ON se.session_id = ss.id AND se.agent_id = :uid
        WHERE ss.session_date BETWEEN :start AND :end
        ORDER BY ss.session_date, ss.session_number
    """), {"uid": user["id"], "start": monday, "end": sunday})
    rows = r.mappings().all()
    result = []
    for row in rows:
        d = dict(row)
        d["id"] = str(d["id"])
        if d.get("session_date"):
            d["session_date"] = d["session_date"].isoformat()
        if d.get("entered_at"):
            d["entered_at"] = d["entered_at"].isoformat()
        result.append(d)
    return result


@router.post("/sprint/log")
async def log_sprint(
    body: SprintLog,
    user=Depends(require_role("agent", "ceo", "admin")),
    db: AsyncSession = Depends(get_db),
):
    if body.session_number not in (1, 2, 3):
        raise HTTPException(status_code=400, detail="session_number must be 1, 2, or 3")

    r = await db.execute(text("""
        INSERT INTO sprint_sessions (session_date, session_number)
        VALUES (:d, :n)
        ON CONFLICT (session_date, session_number)
        DO UPDATE SET session_date = EXCLUDED.session_date
        RETURNING id
    """), {"d": body.session_date, "n": body.session_number})
    session_id = r.scalar()

    await db.execute(text("""
        INSERT INTO sprint_entries (session_id, agent_id, calls_made, leads_generated, meetings_booked)
        VALUES (:sid, :uid, :calls, :leads, :meetings)
        ON CONFLICT (session_id, agent_id) DO UPDATE SET
            calls_made      = :calls,
            leads_generated = :leads,
            meetings_booked = :meetings,
            entered_at      = NOW()
    """), {
        "sid": session_id, "uid": user["id"],
        "calls": body.calls_made, "leads": body.leads_generated, "meetings": body.meetings_booked,
    })
    await db.commit()
    return {"ok": True}


class GpsGoals(BaseModel):
    annual_gci: int = 0
    units_target: int = 0
    listings_target: int = 0
    buyers_target: int = 0


@router.get("/gps")
async def get_gps(
    user=Depends(require_role("agent", "ceo", "admin")),
    db: AsyncSession = Depends(get_db),
):
    try:
        r = await db.execute(text("""
            SELECT annual_gci, units_target, listings_target, buyers_target, year
            FROM gps_goals WHERE agent_id = :uid ORDER BY year DESC LIMIT 1
        """), {"uid": user["id"]})
        row = r.mappings().first()
        return dict(row) if row else {"annual_gci": 0, "units_target": 0, "listings_target": 0, "buyers_target": 0, "year": datetime.now().year}
    except Exception:
        return {"annual_gci": 0, "units_target": 0, "listings_target": 0, "buyers_target": 0, "year": datetime.now().year}


@router.put("/gps")
async def update_gps(
    body: GpsGoals,
    user=Depends(require_role("agent", "ceo", "admin")),
    db: AsyncSession = Depends(get_db),
):
    year = datetime.now().year
    await db.execute(text("""
        INSERT INTO gps_goals (agent_id, year, annual_gci, units_target, listings_target, buyers_target)
        VALUES (:uid, :year, :gci, :units, :listings, :buyers)
        ON CONFLICT (agent_id) DO UPDATE SET
            annual_gci      = :gci,
            units_target    = :units,
            listings_target = :listings,
            buyers_target   = :buyers,
            year            = :year,
            updated_at      = NOW()
    """), {
        "uid": user["id"], "year": year,
        "gci": body.annual_gci, "units": body.units_target,
        "listings": body.listings_target, "buyers": body.buyers_target,
    })
    await db.commit()
    return {"ok": True}
