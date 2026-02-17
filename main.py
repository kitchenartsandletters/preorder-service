from fastapi import FastAPI
from routes.webhooks import router as webhooks_router
from routes.approvals import router as approvals_router
from routes.reclassify import router as reclassify_router

app = FastAPI(title="preorder-service")

app.include_router(webhooks_router)
app.include_router(approvals_router)
app.include_router(reclassify_router)

@app.get("/healthz")
def healthz(): return {"ok": True}