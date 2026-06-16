"""
KWAC OS -- Messages Router
In-app collaboration messaging between agents
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from database import get_db
from auth import require_role

router = APIRouter()


class MessageSend(BaseModel):
    to_id: str
    property_code: Optional[str] = None
    body: str


@router.get("/inbox")
async def inbox(
    user=Depends(require_role("agent", "ceo", "admin")),
    db: AsyncSession = Depends(get_db),
):
    r = await db.execute(text("""
        SELECT
            m.id, m.from_id, m.to_id, m.property_code,
            m.body, m.is_read, m.created_at,
            u.full_name AS from_name,
            a.phone AS from_phone,
            u.email AS from_email
        FROM messages m
        LEFT JOIN users u ON u.id = m.from_id
        LEFT JOIN agents a ON a.id = m.from_id
        WHERE m.to_id = :uid
        ORDER BY m.created_at DESC
        LIMIT 50
    """), {"uid": user["id"]})
    rows = r.mappings().all()
    return [
        {
            "id": str(row["id"]),
            "from_id": str(row["from_id"]),
            "from_name": row["from_name"],
            "from_phone": row["from_phone"],
            "from_email": row["from_email"],
            "property_code": row["property_code"],
            "body": row["body"],
            "is_read": row["is_read"],
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        }
        for row in rows
    ]


@router.get("/unread-count")
async def unread_count(
    user=Depends(require_role("agent", "ceo", "admin")),
    db: AsyncSession = Depends(get_db),
):
    r = await db.execute(
        text("SELECT COUNT(*) FROM messages WHERE to_id=:uid AND is_read=FALSE"),
        {"uid": user["id"]},
    )
    return {"count": r.scalar()}


@router.post("/send")
async def send_message(
    body: MessageSend,
    user=Depends(require_role("agent", "ceo", "admin")),
    db: AsyncSession = Depends(get_db),
):
    if body.to_id == user["id"]:
        raise HTTPException(status_code=400, detail="Den mporeite na steilete minima ston eauto sas")
    rec = await db.execute(text("SELECT id FROM users WHERE id=:id"), {"id": body.to_id})
    if not rec.first():
        raise HTTPException(status_code=404, detail="Paraliptis den vrethike")
    await db.execute(text("""
        INSERT INTO messages (from_id, to_id, property_code, body)
        VALUES (:from_id, :to_id, :code, :body)
    """), {
        "from_id": user["id"],
        "to_id": body.to_id,
        "code": body.property_code,
        "body": body.body,
    })
    await db.commit()
    return {"ok": True}


@router.put("/{msg_id}/read")
async def mark_read(
    msg_id: str,
    user=Depends(require_role("agent", "ceo", "admin")),
    db: AsyncSession = Depends(get_db),
):
    await db.execute(
        text("UPDATE messages SET is_read=TRUE WHERE id=:id AND to_id=:uid"),
        {"id": msg_id, "uid": user["id"]},
    )
    await db.commit()
    return {"ok": True}


@router.put("/read-all")
async def mark_all_read(
    user=Depends(require_role("agent", "ceo", "admin")),
    db: AsyncSession = Depends(get_db),
):
    await db.execute(
        text("UPDATE messages SET is_read=TRUE WHERE to_id=:uid"),
        {"uid": user["id"]},
    )
    await db.commit()
    return {"ok": True}
