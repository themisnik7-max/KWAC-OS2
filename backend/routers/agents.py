"""
KWAC OS -- Agents Router
/me, /goals, /weekly/current, /weekly/submit,
/leaderboard, /sprint, /sprint/log, /gps GET+PUT
"""
from datetime import date, timedelta
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
    "sale_contracts", "purchase_contracts", "rental_contracts", "photo_shoots",
    "open_houses", "matterport_scans", "floor_plans", "new_partners", "referrals_given",
    "trainings_attended", "team_meetings", "conferences",
]

XP_WEIGHTS = {
    "cold_calls": 1, "social_media_leads": 3, "mail_leads": 2, "portal_leads": 2,
    "referrals": 5, "followup_calls": 1, "first_meetings": 8, "second_meetings": 10,
    "meetings_with_seller": 8, "meetings_with_buyer": 8, "meetings_with_tenant": 6,
    "exclusive_listings": 30, "simple_listings": 15, "sale_contracts": 100,
    "purchase_contracts": 100, "rental_contracts": 40, "photo_shoots": 5,
    "open_houses": 10, "matterport_scans": 8, "floor_plans": 5,
    "new_partners": 10, "referrals_given": 10, "trainings_attended": 5,
    "team_meetings": 3, "conferences": 15,
}


def _monday() -> date:
    today = date.today()
    return today - timedelta(days=today.weekday())


def _calc_xp(data: dict) -> int:
    return sum(int(data.get(k, 0)) * XP_WEIGHTS.get(k, 0) for k in METRIC_KEYS)


# -- /me --------------------------------------------------------------
@router.get("/me")
async def agent_me(
    user=Depends(require_role("agent", "ceo", "admin")),
    db: AsyncSession = Depends(get_db),
):
    r = await db.execute(text("""
        SELECT u.id, u.email, u.full_name, u.role,
            COALESCE(a.xp_total, 0)     AS xp_total,
            COALESCE(a.xp_this_week, 0) AS xp_this_week,
            COALESCE(a.level, 1)         AS level,
            COALESCE(a.streak_weeks, 0)  AS streak_weeks,
            a.team, a.phone
        FROM users u LEFT JOIN agents a ON a.id = u.id
        WHERE u.id = :id
    """), {"id": user["id"]})
    row = r.mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Xrhsths den vrethike")
    return dict(row)


# -- /goals -----------------------------------------------------------
@router.get("/goals")
async def agent_goals(
    user=Depends(require_role("agent", "ceo", "admin")),
    db: AsyncSession = Depends(get_db),
):
    r = await db.execute(text(
        "SELECT metric, target, label_el FROM agent_goals WHERE agent_id = :id ORDER BY sort_order"
    ), {"id": user["id"]})
    return [dict(row) for row in r.mappings().all()]


# -- /weekly/current --------------------------------------------------
@router.get("/weekly/current")
async def weekly_current(
    user=Depends(require_role("agent", "ceo", "admin")),
    db: AsyncSession = Depends(get_db),
):
    monday = _monday()
    r = await db.execute(text(
        "SELECT * FROM weekly_submissions WHERE agent_id = :id AND week_start = :monday"
    ), {"id": user["id"], "monday": monday})
    row = r.mappings().first()
    if not row:
        return {"week_start": str(monday), "xp_earned": 0, "submitted_at": None}
    return dict(row)


# -- /weekly/submit ---------------------------------------------------
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


@router.post("/weekly/submit")
async def weekly_submit(
    body: WeeklySubmit,
    user=Depends(require_role("agent", "ceo", "admin")),
    db: AsyncSession = Depends(get_db),
):
    monday = _monday()
    data = body.model_dump()
    xp = _calc_xp(data)
    cols = ", ".join(METRIC_KEYS)
    vals = ", ".join(":" + k for k in METRIC_KEYS)
    updates = ", ".join(k + "=:" + k for k in METRIC_KEYS)
    params = {k: data[k] for k in METRIC_KEYS}
    params.update({"uid": user["id"], "monday": monday, "xp": xp, "notes": data.get("notes")})

    await db.execute(text(
        "INSERT INTO weekly_submissions (agent_id, week_start, " + cols + ", xp_earned, notes, submitted_at) "
        "VALUES (:uid, :monday, " + vals + ", :xp, :notes, NOW()) "
        "ON CONFLICT (agent_id, week_start) DO UPDATE SET "
        + updates + ", xp_earned=:xp, notes=:notes, submitted_at=NOW()"
    ), params)

    await db.execute(text("""
        INSERT INTO agents (id, xp_total, xp_this_week, last_submitted)
        VALUES (:uid,
            (SELECT COALESCE(SUM(xp_earned),0) FROM weekly_submissions WHERE agent_id=:uid),
            :xp, :ws)
        ON CONFLICT (id) DO UPDATE SET
            xp_total=(SELECT COALESCE(SUM(xp_earned),0) FROM weekly_submissions WHERE agent_id=:uid),
            xp_this_week=:xp, last_submitted=:ws, updated_at=NOW()
    """), {"uid": user["id"], "xp": xp, "ws": monday})

    await db.commit()
    return {"ok": True, "xp_earned": xp}


# -- /leaderboard -----------------------------------------------------
@router.get("/leaderboard")
async def leaderboard(
    user=Depends(require_role("agent", "ceo", "admin")),
    db: AsyncSession = Depends(get_db),
):
    monday = _monday()
    r = await db.execute(text("""
        SELECT u.id, u.full_name,
            COALESCE(a.xp_total, 0)     AS xp_total,
            COALESCE(a.xp_this_week, 0) AS xp_this_week,
            COALESCE(a.level, 1)         AS level,
            COALESCE(a.streak_weeks, 0)  AS streak_weeks,
            a.team,
            (ws.submitted_at IS NOT NULL) AS submitted_at
        FROM users u
        LEFT JOIN agents a ON a.id = u.id
        LEFT JOIN weekly_submissions ws ON ws.agent_id = u.id AND ws.week_start = :monday
        WHERE u.is_active = TRUE AND u.role IN ('agent', 'ceo')
        ORDER BY a.xp_this_week DESC NULLS LAST, a.xp_total DESC NULLS LAST
        LIMIT 50
    """), {"monday": monday})
    return [
        {
            "id": str(row["id"]),
            "full_name": row["full_name"],
            "xp_total": row["xp_total"],
            "xp_this_week": row["xp_this_week"],
            "level": row["level"],
            "streak_weeks": row["streak_weeks"],
            "team": row["team"],
            "submitted_at": str(row["submitted_at"]) if row["submitted_at"] else None,
        }
        for row in r.mappings().all()
    ]


# -- /sprint ----------------------------------------------------------
@router.get("/sprint")
async def sprint_today(
    user=Depends(require_role("agent", "ceo", "admin")),
    db: AsyncSession = Depends(get_db),
):
    monday = _monday()
    today = date.today()
    today_r = await db.execute(text("""
        SELECT id, session_date, calls_made, leads, appointments, duration_minutes, notes, created_at
        FROM sprint_sessions
        WHERE agent_id = :uid AND session_date = :today
        ORDER BY created_at
    """), {"uid": user["id"], "today": today})
    week_r = await db.execute(text("""
        SELECT SUM(calls_made) AS total_calls, SUM(leads) AS total_leads,
               SUM(appointments) AS total_appointments, SUM(duration_minutes) AS total_minutes,
               COUNT(*) AS total_sessions
        FROM sprint_sessions
        WHERE agent_id = :uid AND session_date >= :monday AND session_date <= :today
    """), {"uid": user["id"], "monday": monday, "today": today})
    week_row = week_r.mappings().first()
    return {
        "today": [
            {
                "id": str(r["id"]),
                "calls_made": r["calls_made"],
                "leads": r["leads"] or 0,
                "appointments": r["appointments"] or 0,
                "duration_minutes": r["duration_minutes"],
                "notes": r["notes"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            }
            for r in today_r.mappings().all()
        ],
        "week_totals": {
            "total_calls": int(week_row["total_calls"] or 0),
            "total_leads": int(week_row["total_leads"] or 0),
            "total_appointments": int(week_row["total_appointments"] or 0),
            "total_minutes": int(week_row["total_minutes"] or 0),
            "total_sessions": int(week_row["total_sessions"] or 0),
        },
    }


class SprintLog(BaseModel):
    calls_made: int = 0
    leads: int = 0
    appointments: int = 0
    duration_minutes: int = 0
    notes: Optional[str] = None


@router.post("/sprint/log")
async def sprint_log(
    body: SprintLog,
    user=Depends(require_role("agent", "ceo", "admin")),
    db: AsyncSession = Depends(get_db),
):
    await db.execute(text("""
        INSERT INTO sprint_sessions
            (agent_id, session_date, calls_made, leads, appointments, duration_minutes, notes)
        VALUES (:uid, CURRENT_DATE, :calls, :leads, :appts, :mins, :notes)
    """), {
        "uid": user["id"], "calls": body.calls_made, "leads": body.leads,
        "appts": body.appointments, "mins": body.duration_minutes, "notes": body.notes,
    })
    await db.commit()
    return {"ok": True}


# -- /gps -------------------------------------------------------------
@router.get("/gps")
async def gps_get(
    user=Depends(require_role("agent", "ceo", "admin")),
    db: AsyncSession = Depends(get_db),
):
    r = await db.execute(text(
        "SELECT annual_gci, units_target, listings_target, buyers_target, year FROM gps_goals WHERE agent_id = :id"
    ), {"id": user["id"]})
    row = r.mappings().first()
    if not row:
        return {"annual_gci": 0, "units_target": 0, "listings_target": 0, "buyers_target": 0, "year": date.today().year}
    return dict(row)


class GPSUpdate(BaseModel):
    annual_gci: int = 0
    units_target: int = 0
    listings_target: int = 0
    buyers_target: int = 0


@router.put("/gps")
async def gps_put(
    body: GPSUpdate,
    user=Depends(require_role("agent", "ceo", "admin")),
    db: AsyncSession = Depends(get_db),
):
    await db.execute(text("""
        INSERT INTO gps_goals (agent_id, annual_gci, units_target, listings_target, buyers_target)
        VALUES (:uid, :gci, :units, :listings, :buyers)
        ON CONFLICT (agent_id) DO UPDATE SET
            annual_gci=:gci, units_target=:units, listings_target=:listings,
            buyers_target=:buyers, updated_at=NOW()
    """), {"uid": user["id"], "gci": body.annual_gci, "units": body.units_target,
           "listings": body.listings_target, "buyers": body.buyers_target})
    await db.commit()
    return {"ok": True}
