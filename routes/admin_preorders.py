from fastapi import APIRouter, HTTPException, Header, Depends
import os
from supabase import create_client, Client

from dotenv import load_dotenv
load_dotenv()

router = APIRouter()

# ------------------------------------------------------------------
# Environment
# ------------------------------------------------------------------

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
ADMIN_TOKEN = os.getenv("PREORDER_ADMIN_TOKEN")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)


# ------------------------------------------------------------------
# Auth
# ------------------------------------------------------------------

def require_admin_token(x_admin_token: str = Header(default="")):
    if not ADMIN_TOKEN or x_admin_token != ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="Forbidden")
    return True


# ------------------------------------------------------------------
# Preorder Dashboard Endpoints
# ------------------------------------------------------------------

@router.get("/products")
def get_preorder_products(ok: bool = Depends(require_admin_token)):
    resp = (
        supabase
        .schema("preorder")
        .from_("vw_preorder_products")
        .select("*")
        .execute()
    )

    return resp.data or []


@router.get("/release-queue")
def get_release_queue(ok: bool = Depends(require_admin_token)):
    resp = (
        supabase
        .schema("preorder")
        .from_("vw_preorder_release_queue")
        .select("*")
        .execute()
    )

    return resp.data or []


@router.get("/metrics")
def get_preorder_metrics(ok: bool = Depends(require_admin_token)):
    resp = (
        supabase
        .schema("preorder")
        .from_("vw_preorder_metrics")
        .select("*")
        .single()
        .execute()
    )

    return resp.data or {
        "active_preorders": 0,
        "early_arrivals": 0,
        "anomalies": 0,
        "release_queue_count": 0,
        "released_this_week": 0
    }

@router.get("/live-metrics")
def get_live_presale_metrics(ok: bool = Depends(require_admin_token)):
    """
    Returns presale metrics derived exclusively from verified Tier 1 data
    (post-cutover live webhook events only).

    data_confidence will always be 'verified' for rows in this response.
    For estimated figures covering pre-cutover history, use /products
    which surfaces both live_presale_qty and estimated_presale_qty with
    explicit data_confidence labeling.
    """
    resp = (
        supabase
        .schema("preorder")
        .from_("vw_live_presale_metrics")
        .select("*")
        .execute()
    )

    return resp.data or []