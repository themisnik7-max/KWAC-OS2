"""
KWAC OS -- Admin Router
CEO and admin only: agent management, system-wide stats
"""
from datetime import date, timedelta
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from database import get_db
from auth import require_role, hash_password

router = APIRouter()


@router.get("/stats")
async def system_stats(
    user=Depends(require_role("ceo", "admin")),
    db: AsyncSession = Depends(get_db),
):
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    r = await db.execute(text("""
        SELECT
            (SELECT COUNT(*) FROM users WHERE is_active = TRUE)                                              AS total_users,
            (SELECT COUNT(*) FROM users WHERE is_active = TRUE AND role = 'agent')                           AS total_agents,
            (SELECT COUNT(*) FROM properties)                                                                AS total_properties,
            (SELECT COUNT(*) FROM people)                                                                    AS total_contacts,
            (SELECT COUNT(*) FROM weekly_submissions WHERE week_start = :monday AND submitted_at IS NOT NULL) AS submitted_this_week,
            (SELECT COALESCE(SUM(xp_earned), 0) FROM weekly_submissions WHERE week_start = :monday)          AS total_xp_this_week
    """), {"monday": monday})
    row = r.mappings().first()
    return dict(row) if row else {}


@router.get("/agents")
async def list_agents(
    user=Depends(require_role("ceo", "admin")),
    db: AsyncSession = Depends(get_db),
):
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    r = await db.execute(text("""
        SELECT
            u.id, u.email, u.full_name, u.role, u.is_active, u.created_at,
            COALESCE(a.xp_total, 0)     AS xp_total,
            COALESCE(a.xp_this_week, 0) AS xp_this_week,
            COALESCE(a.level, 1)         AS level,
            COALESCE(a.streak_weeks, 0)  AS streak_weeks,
            a.team, a.phone, a.last_submitted,
            (ws.submitted_at IS NOT NULL) AS submitted_this_week
        FROM users u
        LEFT JOIN agents a ON a.id = u.id
        LEFT JOIN weekly_submissions ws ON ws.agent_id = u.id AND ws.week_start = :monday
        ORDER BY u.full_name
    """), {"monday": monday})
    rows = r.mappings().all()
    return [
        {
            "id": str(row["id"]),
            "email": row["email"],
            "full_name": row["full_name"],
            "role": row["role"],
            "is_active": row["is_active"],
            "xp_total": row["xp_total"],
            "xp_this_week": row["xp_this_week"],
            "level": row["level"],
            "streak_weeks": row["streak_weeks"],
            "team": row["team"],
            "phone": row["phone"],
            "last_submitted": row["last_submitted"].isoformat() if row["last_submitted"] else None,
            "submitted_this_week": bool(row["submitted_this_week"]),
        }
        for row in rows
    ]


class AgentCreate(BaseModel):
    email: EmailStr
    full_name: str
    password: str
    role: str = "agent"
    team: Optional[str] = None
    phone: Optional[str] = None


@router.post("/agents")
async def create_agent(
    body: AgentCreate,
    user=Depends(require_role("ceo", "admin")),
    db: AsyncSession = Depends(get_db),
):
    if body.role not in ("agent", "ceo", "admin"):
        raise HTTPException(status_code=400, detail="Invalid role.")
    existing = await db.execute(text("SELECT id FROM users WHERE email = :email"), {"email": body.email.lower()})
    if existing.first():
        raise HTTPException(status_code=400, detail="Email already exists.")
    r = await db.execute(text("""
        INSERT INTO users (email, password_hash, full_name, role)
        VALUES (:email, :hash, :name, :role)
        RETURNING id
    """), {"email": body.email.lower(), "hash": hash_password(body.password), "name": body.full_name, "role": body.role})
    new_id = r.scalar()
    await db.execute(text("INSERT INTO agents (id, team, phone) VALUES (:id, :team, :phone)"),
                     {"id": new_id, "team": body.team, "phone": body.phone})
    await db.commit()
    return {"ok": True, "id": str(new_id)}


class AgentUpdate(BaseModel):
    full_name: Optional[str] = None
    role: Optional[str] = None
    team: Optional[str] = None
    phone: Optional[str] = None
    is_active: Optional[bool] = None
    new_password: Optional[str] = None


@router.put("/agents/{agent_id}")
async def update_agent(
    agent_id: str,
    body: AgentUpdate,
    user=Depends(require_role("ceo", "admin")),
    db: AsyncSession = Depends(get_db),
):
    user_updates = []
    user_params: dict = {"id": agent_id}
    if body.full_name is not None:
        user_updates.append("full_name = :full_name"); user_params["full_name"] = body.full_name
    if body.role is not None:
        user_updates.append("role = :role"); user_params["role"] = body.role
    if body.is_active is not None:
        user_updates.append("is_active = :is_active"); user_params["is_active"] = body.is_active
    if body.new_password:
        user_updates.append("password_hash = :pw"); user_params["pw"] = hash_password(body.new_password)
    if user_updates:
        await db.execute(text(f"UPDATE users SET {', '.join(user_updates)} WHERE id = :id"), user_params)

    agent_updates = []
    agent_params: dict = {"id": agent_id}
    if body.team is not None:
        agent_updates.append("team = :team"); agent_params["team"] = body.team
    if body.phone is not None:
        agent_updates.append("phone = :phone"); agent_params["phone"] = body.phone
    if agent_updates:
        await db.execute(text(f"""
            INSERT INTO agents (id, team, phone) VALUES (:id, :team, :phone)
            ON CONFLICT (id) DO UPDATE SET {', '.join(agent_updates)}, updated_at = NOW()
        """), {"id": agent_id, "team": body.team, "phone": body.phone})

    await db.commit()
    return {"ok": True}
