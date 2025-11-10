from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from ..db.connection import get_pool

router = APIRouter(prefix="/approvals")

class ApproveIn(BaseModel):
    variant_id: int
    product_id: Optional[int] = None
    isbn: Optional[str] = None
    title: Optional[str] = None
    pub_date: Optional[str] = None     # YYYY-MM-DD
    override_pub_date: Optional[str] = None
    approved_by: str
    notes: Optional[str] = None

@router.get("/list")
async def approvals_list():
    pool = await get_pool()
    rows = await pool.fetch("select * from preorder.vw_pending_approvals order by effective_pub_date nulls last limit 200")
    return [dict(r) for r in rows]

@router.post("/approve")
async def approvals_approve(body: ApproveIn):
    pool = await get_pool()
    # upsert-style: if row exists for variant_id, update; else insert
    existing = await pool.fetchrow("select id from preorder.approvals where variant_id = $1 and active is true", body.variant_id)
    if existing:
        await pool.execute("""
            update preorder.approvals
            set approved = true,
                approved_by = $2,
                approved_at = now(),
                notes = coalesce($3, notes),
                product_id = coalesce($4, product_id),
                isbn = coalesce($5, isbn),
                title = coalesce($6, title),
                pub_date = coalesce($7::date, pub_date),
                override_pub_date = coalesce($8::date, override_pub_date),
                updated_at = now()
            where id = $1
        """, existing["id"], body.approved_by, body.notes, body.product_id, body.isbn, body.title, body.pub_date, body.override_pub_date)
    else:
        await pool.execute("""
            insert into preorder.approvals
            (product_id, variant_id, isbn, title, pub_date, override_pub_date,
             approved, approved_by, approved_at, notes, active)
            values ($1, $2, $3, $4, $5::date, $6::date, true, $7, now(), $8, true)
        """, body.product_id, body.variant_id, body.isbn, body.title, body.pub_date, body.override_pub_date, body.approved_by, body.notes)
    return {"ok": True}

@router.post("/revoke")
async def approvals_revoke(variant_id: int, who: str):
    pool = await get_pool()
    res = await pool.execute("""
        update preorder.approvals
        set approved = false,
            approved_by = $2,
            approved_at = now(),
            updated_at = now()
        where variant_id = $1 and active is true
    """, variant_id, who)
    if res == "UPDATE 0":
        raise HTTPException(status_code=404, detail="No active approval for that variant_id")
    return {"ok": True}