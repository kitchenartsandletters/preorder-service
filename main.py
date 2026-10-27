from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import logging
from routes.webhooks import router as webhooks_router
from routes.reclassify import router as reclassify_router
from routes.admin_preorders import router as admin_preorders_router
from routes.internal_events import router as internal_events_router
from routes.admin_cleanup import router as admin_cleanup_router
from routes.admin_holds import router as admin_holds_router
from routes.admin_pubdate import router as admin_pubdate_router
from routes.admin_shipping import router as admin_shipping_router
from routes.admin_tagger import router as admin_tagger_router
from routes.admin_nyt import router as admin_nyt_router


app = FastAPI(title="preorder-service")

# CORS for admin dashboard
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "https://admin.kitchenartsandletters.com"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

logger = logging.getLogger("uvicorn.error")
logger.info("🚀 Preorder service fully started and accepting requests")

app.include_router(webhooks_router)
app.include_router(reclassify_router)

# routes/approvals.py is deliberately NOT mounted (docs/DOCS_STATUS.md Landmine 9).
# It had no auth dependency — the only app-wide middleware is CORS, which
# restricts browsers, not scripts — and two of its endpoints WROTE to
# preorder.approvals, including override_pub_date. Nothing calls it: no caller
# exists in this repo or in admin-dashboard, and the table is dormant (1 row,
# last touched 2025-11-10). Owner decision 2026-09-22: unmount.
# tests/test_approvals_unmounted.py fails if it is mounted again without auth.

# admin dashboard API
app.include_router(admin_preorders_router, prefix="/admin/preorders", tags=["admin_preorders"]) 

# internal events API
app.include_router(internal_events_router)

# admin cleanup API
app.include_router(admin_cleanup_router, prefix="/admin/preorders", tags=["admin_cleanup"])

app.include_router(admin_holds_router, prefix="/admin/preorders", tags=["admin_holds"])

app.include_router(admin_pubdate_router, prefix="/admin/preorders", tags=["admin_pubdate"])

# admin shipping API
app.include_router(admin_shipping_router, prefix="/admin/preorders", tags=["admin_shipping"])

# admin tagger API
app.include_router(admin_tagger_router, prefix="/admin/preorders", tags=["admin_tagger"])

# admin NYT report API
app.include_router(admin_nyt_router, prefix="/admin/preorders", tags=["admin_nyt"])

@app.get("/healthz")
def healthz(): return {"ok": True}