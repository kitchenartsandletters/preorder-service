from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import logging
from routes.webhooks import router as webhooks_router
from routes.approvals import router as approvals_router
from routes.reclassify import router as reclassify_router
from routes.admin_preorders import router as admin_preorders_router
from routes.internal_events import router as internal_events_router
from routes.admin_cleanup import router as admin_cleanup_router


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
app.include_router(approvals_router)
app.include_router(reclassify_router)

# admin dashboard API
app.include_router(admin_preorders_router, prefix="/admin/preorders", tags=["admin_preorders"]) 

# internal events API
app.include_router(internal_events_router)

# admin cleanup API
app.include_router(admin_cleanup_router, prefix="/admin/cleanup", tags=["admin_cleanup"])

@app.get("/healthz")
def healthz(): return {"ok": True}