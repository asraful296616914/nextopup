"""Run: uvicorn app.main:app --host 0.0.0.0 --port 8000   (env vars: see .env.example)"""
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .core import db, hash_pw, now
from .routes_admin import router as admin_router
from .routes_user import router as user_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.users.create_index("email", unique=True)
    await db.packages.create_index("pkg_no", unique=True)
    await db.orders.create_index([("user_id", 1), ("created_at", -1)])
    await db.topups.create_index([("status", 1), ("created_at", -1)])
    email, pw = os.environ.get("ADMIN_EMAIL"), os.environ.get("ADMIN_PASSWORD")
    if email and pw and not await db.users.find_one({"email": email.lower()}):
        await db.users.insert_one({"name": "Admin", "email": email.lower(), "role": "admin",
                                   "balance": 0, "total_spent": 0, "support_pin": "0000000",
                                   "pw_hash": hash_pw(pw), "created_at": now()})
    yield


app = FastAPI(title="Top-up API", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=os.environ.get("ALLOWED_ORIGINS", "").split(","),
                   allow_methods=["*"], allow_headers=["*"])
app.include_router(user_router)
app.include_router(admin_router)
ROOT = Path(__file__).parent.parent
app.mount("/admin", StaticFiles(directory=ROOT / "static", html=True))
app.mount("/", StaticFiles(directory=ROOT / "public", html=True))  # customer site, keep last
