"""Config, database handle, auth helpers shared by all routers."""

import os
import time
from datetime import datetime, timezone

import bcrypt
import certifi
import jwt
from bson import ObjectId
from fastapi import Depends, Header, HTTPException
from motor.motor_asyncio import AsyncIOMotorClient


# -------------------- CONFIGURATION --------------------

JWT_SECRET = os.environ["JWT_SECRET"]
JWT_TTL = 7 * 24 * 3600

MONGO_URL = os.environ["MONGO_URL"]
DB_NAME = os.environ.get("DB_NAME", "topup")


# -------------------- DATABASE CONNECTION --------------------

mongo_client = AsyncIOMotorClient(
    MONGO_URL,
    tls=True,
    tlsCAFile=certifi.where(),
    serverSelectionTimeoutMS=15000,
    connectTimeoutMS=15000,
)

db = mongo_client[DB_NAME]


# -------------------- MEMBERSHIP TIERS --------------------

TIERS = [
    (100, "Bronze"),
    (1000, "Silver"),
    (5000, "Gold"),
    (10000, "Platinum"),
    (25000, "Diamond"),
    (50000, "Heroic"),
    (100000, "Master"),
]


# -------------------- PRODUCT CATEGORIES --------------------

CATEGORIES = {
    "id_code": "UID Diamond Topup",
    "weekly_monthly": "Weekly / Monthly",
    "weekly_lite": "Weekly Lite",
    "level_up": "Level Up Pass",
    "ff_likes": "FF Likes",
}


# -------------------- DATABASE HELPERS --------------------

async def category_list() -> list:
    """Section titles and image ids (admin-editable), falling back to defaults."""

    saved = {
        c["_id"]: c
        async for c in db.categories.find()
    }

    return [
        {
            "key": k,
            "title": saved.get(k, {}).get("title", d),
            "image_id": saved.get(k, {}).get("image_id"),
        }
        for k, d in CATEGORIES.items()
    ]


def now():
    return datetime.now(timezone.utc)


def level_for(spent: int) -> str:
    return next(
        (name for cap, name in TIERS if spent <= cap),
        "Grandmaster",
    )


# -------------------- PASSWORD HELPERS --------------------

def hash_pw(password: str) -> str:
    return bcrypt.hashpw(
        password.encode(),
        bcrypt.gensalt(),
    ).decode()


def check_pw(password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(
        password.encode(),
        hashed_password.encode(),
    )


# -------------------- AUTHENTICATION --------------------

def make_token(uid: str) -> str:
    return jwt.encode(
        {
            "sub": uid,
            "exp": int(time.time()) + JWT_TTL,
        },
        JWT_SECRET,
        algorithm="HS256",
    )


def oid(s: str) -> ObjectId:
    try:
        return ObjectId(s)
    except Exception:
        raise HTTPException(404, "Not found")


def ser(d: dict, drop=()) -> dict:
    """Mongo document to JSON-safe dictionary."""

    return {
        ("id" if k == "_id" else k): (
            str(v) if isinstance(v, ObjectId) else v
        )
        for k, v in d.items()
        if k not in drop
    }


# -------------------- LEDGER --------------------

async def ledger(user_id, delta: int, reason: str, ref=None):
    await db.ledger.insert_one(
        {
            "user_id": user_id,
            "delta": delta,
            "reason": reason,
            "ref": ref,
            "at": now(),
        }
    )


# -------------------- USER AUTHENTICATION --------------------

async def current_user(authorization: str = Header(default="")):
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Login required")

    try:
        uid = jwt.decode(
            authorization[7:],
            JWT_SECRET,
            algorithms=["HS256"],
        )["sub"]

    except jwt.PyJWTError:
        raise HTTPException(
            401,
            "Session expired, please log in again",
        )

    user = await db.users.find_one(
        {"_id": oid(uid)}
    )

    if not user or user.get("blocked"):
        raise HTTPException(401, "Account unavailable")

    return user


# -------------------- ADMIN AUTHENTICATION --------------------

async def admin_user(user=Depends(current_user)):
    if user["role"] != "admin":
        raise HTTPException(403, "Admin only")

    return user
