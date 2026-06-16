from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from datetime import date, timedelta
from typing import Optional
from pydantic import BaseModel
import json

from database import get_db
from auth import require_role

router = APIRouter()


def current_week():
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)
    return monday, sunday


@router.get("/me")
async def get_my_profile(
    user=Depends(require_role("agent", "ceo", "admin")),
    db: AsyncSession = Depends(get_db),
):
    monday, _ = current_week()
    result = await db.execute(
        text("""
            SELECT u.id, u.email, u.full_name, u.role, u.avatar_url,
                   COALESCE(a.xp_this_week, 0) as xp_this_week,
                   COALESCE(a.level, 1) as level,
                   COALESCE(a.streak_weeks, 0) as streak_weeks,
                   COALESCE((SELECT SUM(xp_earned) FROM weekly_submissions WHERE agent_id = u.id), 0) as xp_total
            FROM users u
            LEFT JOIN agents a ON a.id = u.id
            WHERE u.id = :id
        """),
        {"id": user["id"]},
    )
    row = result.mappings().first()
    if not row:
        raise HTTPException(404, "User not found")
    return dict(row)


@router.get("/weekly/current")
async def get_current_week(
    user=Depends(require_role("agent", "ceo", "admin")),
    db: AsyncSession = Depends(get_db),
):
    monday, sunday = current_week()
    result = await db.execute(
        text("SELECT * FROM weekly_submissions WHERE agent_id = :id AND week_start = :ws"),
        {"id": user["id"], "ws": monday},
    )
    row = result.mappings().first()
    return dict(row) if row else {"week_start": str(monday), "week_end": str(sunday)}


@router.get("/goals")
async def get_goals(
    user=Depends(require_role("agent", "ceo", "admin")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        text("SELECT * FROM weekly_goals WHERE is_active = true ORDER BY sort_order")
    )
    return [dict(r) for r in result.mappings().all()]


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
async def submit_weekly(
    body: WeeklySubmit,
    user=Depends(require_role("agent", "ceo", "admin")),
    db: AsyncSession = Depends(get_db),
):
    monday, sunday = current_week()
    goals_r = await db.execute(
        text("SELECT metric, xp_value, xp_bonus, target FROM weekly_goals WHERE is_active = true")
    )
    goals = goals_r.mappings().all()
    data = body.dict()
    xp = 0
    goals_hit = {}
    for g in goals:
        val = data.get(g["metric"], 0)
        xp += val * g["xp_value"]
        hit = val >= g["target"]
        goals_hit[g["metric"]] = hit
        if hit:
            xp += g["xp_bonus"]

    await db.execute(
        text("""
            INSERT INTO weekly_submissions (
                agent_id, week_start, week_end, submitted_at, xp_earned, goals_hit,
                cold_calls, social_media_leads, mail_leads, portal_leads, referrals,
                followup_calls, first_meetings, second_meetings, meetings_with_seller,
                meetings_with_buyer, meetings_with_tenant, exclusive_listings, simple_listings,
                sale_contracts, purchase_contracts, rental_contracts, photo_shoots, open_houses,
                matterport_scans, floor_plans, new_partners, referrals_given,
                trainings_attended, team_meetings, conferences, notes
            ) VALUES (
                :agent_id, :week_start, :week_end, NOW(), :xp, :goals_hit::jsonb,
                :cold_calls, :social_media_leads, :mail_leads, :portal_leads, :referrals,
                :followup_calls, :first_meetings, :second_meetings, :meetings_with_seller,
                :meetings_with_buyer, :meetings_with_tenant, :exclusive_listings, :simple_listings,
                :sale_contracts, :purchase_contracts, :rental_contracts, :photo_shoots, :open_houses,
                :matterport_scans, :floor_plans, :new_partners, :referrals_given,
                :trainings_attended, :team_meetings, :conferences, :notes
            )
            ON CONFLICT (agent_id, week_start) DO UPDATE SET
                submitted_at=NOW(), xp_earned=:xp, goals_hit=:goals_hit::jsonb,
                cold_calls=:cold_calls, social_media_leads=:social_media_leads,
                mail_leads=:mail_leads, portal_leads=:portal_leads, referrals=:referrals,
                followup_calls=:followup_calls, first_meetings=:first_meetings,
                second_meetings=:second_meetings, meetings_with_seller=:meetings_with_seller,
                meetings_with_buyer=:meetings_with_buyer, meetings_with_tenant=:meetings_with_tenant,
                exclusive_listings=:exclusive_listings, simple_listings=:simple_listings,
                sale_contracts=:sale_contracts, purchase_contracts=:purchase_contracts,
                rental_contracts=:rental_contracts, photo_shoots=:photo_shoots,
                open_houses=:open_houses, matterport_scans=:matterport_scans,
                floor_plans=:floor_plans, new_partners=:new_partners,
                referrals_given=:referrals_given, trainings_attended=:trainings_attended,
                team_meetings=:team_meetings, conferences=:conferences, notes=:notes
        """),
        {"agent_id": user["id"], "week_start": monday, "week_end": sunday,
         "xp": xp, "goals_hit": json.dumps(goals_hit), **{k: v for k, v in data.items()}},
    )
    await db.execute(
        text("INSERT INTO agents (id, xp_this_week) VALUES (:id, :xp) ON CONFLICT (id) DO UPDATE SET xp_this_week=:xp"),
        {"id": user["id"], "xp": xp},
    )
    await db.commit()
    return {"ok": True, "xp_earned": xp, "goals_hit": goals_hit}


@router.get("/leaderboard")
async def get_leaderboard(
    user=Depends(require_role("agent", "ceo", "admin")),
    db: AsyncSession = Depends(get_db),
):
    monday, _ = current_week()
    result = await db.execute(
        text("""
            SELECT u.full_name, u.email,
                   COALESCE(SUM(ws_all.xp_earned), 0) as xp_total,
                   COALESCE(week_ws.xp_earned, 0) as xp_this_week,
                   week_ws.submitted_at
            FROM users u
            LEFT JOIN weekly_submissions ws_all ON ws_all.agent_id = u.id
            LEFT JOIN weekly_submissions week_ws ON week_ws.agent_id = u.id AND week_ws.week_start = :monday
            WHERE u.role IN ('agent','ceo','admin') AND u.is_active = true
            GROUP BY u.id, u.full_name, u.email, week_ws.xp_earned, week_ws.submitted_at
            ORDER BY xp_total DESC
        """),
        {"monday": monday},
    )
    return [dict(r) for r in result.mappings().all()]
