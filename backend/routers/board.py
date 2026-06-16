"""
KWAC OS -- Board Router
Handles: announcements (read for all, write for CEO/admin)
"""
from typing import Optional
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from database import get_db
from auth import require_role

router = APIRouter()


class AnnouncementCreate(BaseModel):
    title: str
    body: Optional[str] = None
    type: str = "other"
    expires_at: Optional[str] = None


@router.get("/announcements")
async def list_announcements(
    user=Depends(require_role("agent", "ceo", "admin")),
    db: AsyncSession = Depends(get_db),
):
    r = await db.execute(text("""
        SELECT
            ba.id, ba.title, ba.body, ba.type, ba.expires_at, ba.created_at,
            u.full_name AS author_name, u.role AS author_role
        FROM board_announcements ba
        LEFT JOIN users u ON u.id = ba.author_id
        WHERE ba.expires_at IS NULL OR ba.expires_at >= CURRENT_DATE
        ORDER BY ba.created_at DESC
        LIMIT 60
    """))
    rows = r.mappings().all()
    return [
        {
            "id": str(row["id"]),
            "title": row["title"],
            "body": row["body"],
            "type": row["type"],
            "author_name": row["author_name"],
            "author_role": row["author_role"],
            "expires_at": row["expires_at"].isoformat() if row["expires_at"] else None,
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        }
        for row in rows
    ]


@router.post("/announcements")
async def create_announcement(
    body: AnnouncementCreate,
    user=Depends(require_role("ceo", "admin")),
    db: AsyncSession = Depends(get_db),
):
    await db.execute(text("""
        INSERT INTO board_announcements (author_id, type, title, body, expires_at)
        VALUES (:uid, :type, :title, :body, :expires::date)
    """), {
        "uid": user["id"], "type": body.type, "title": body.title,
        "body": body.body, "expires": body.expires_at,
    })
    await db.commit()
    return {"ok": True}


@router.delete("/announcements/{ann_id}")
async def delete_announcement(
    ann_id: str,
    user=Depends(require_role("ceo", "admin")),
    db: AsyncSession = Depends(get_db),
):
    await db.execute(text("DELETE FROM board_announcements WHERE id = :id"), {"id": ann_id})
    await db.commit()
    return {"ok": True}
