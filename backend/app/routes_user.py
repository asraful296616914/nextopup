"""Customer-facing API. Customers never receive internal package numbers (pkg_no)."""
import asyncio
import secrets
from datetime import timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Response
from pymongo.errors import DuplicateKeyError

from .core import (category_list, check_pw, current_user, db, hash_pw, ledger, level_for, make_token,
                   now, oid, ser)
from .schemas import LoginIn, OrderIn, PlayerIn, RegisterIn, TopupIn

router = APIRouter(prefix="/api")
PRIVATE_USER = ("pw_hash", "blocked")


async def claim_trx(trx: str):
    """A Transaction ID can be used once across orders and add-money requests."""
    try:
        await db.trx_ids.insert_one({"_id": trx.upper()})
    except DuplicateKeyError:
        raise HTTPException(409, "This Transaction ID was already used")


@router.post("/auth/register")
async def register(b: RegisterIn):
    doc = {"name": b.name.strip(), "email": b.email.lower(), "role": "user", "balance": 0,
           "total_spent": 0, "support_pin": f"{secrets.randbelow(10**7):07d}",
           "pw_hash": await asyncio.to_thread(hash_pw, b.password), "created_at": now()}
    try:
        res = await db.users.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "Email already registered")
    return {"token": make_token(str(res.inserted_id))}


@router.post("/auth/login")
async def login(b: LoginIn):
    u = await db.users.find_one({"email": b.email.lower()})
    ok = u and await asyncio.to_thread(check_pw, b.password, u["pw_hash"])
    if not ok or u.get("blocked"):
        raise HTTPException(401, "Wrong email or password")
    return {"token": make_token(str(u["_id"])), "role": u["role"]}


@router.get("/me")
async def me(u=Depends(current_user)):
    return {**ser(u, PRIVATE_USER), "level": level_for(u["total_spent"])}


@router.get("/me/stats")
async def my_stats(u=Depends(current_user)):
    """Numbers for My Account: this month (Bangladesh time) and all-time."""
    bd = timezone(timedelta(hours=6))
    start = now().astimezone(bd).replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    async def total(coll, match, field):
        r = await coll.aggregate([{"$match": {"user_id": u["_id"], **match}},
                                  {"$group": {"_id": None, "sum": {"$sum": "$" + field}, "n": {"$sum": 1}}}]).to_list(1)
        return (r[0]["sum"], r[0]["n"]) if r else (0, 0)

    spent, n = await total(db.orders, {"status": "completed", "updated_at": {"$gte": start}}, "price")
    added, _ = await total(db.topups, {"status": "approved", "updated_at": {"$gte": start}}, "amount")
    return {"month_topup_total": spent, "month_orders": n, "month_wallet_added": added,
            "total_orders": await db.orders.count_documents({"user_id": u["_id"]}),
            "total_spent": u["total_spent"], "balance": u["balance"], "level": level_for(u["total_spent"])}


@router.get("/categories")
async def categories():
    return [{"key": c["key"], "title": c["title"],
             "image": f"/api/img/{c['image_id']}" if c["image_id"] else None}
            for c in await category_list()]


@router.get("/home")
async def home():
    """Everything the home page needs in one request."""
    s = await db.settings.find_one({"_id": "site"}) or {}

    def url(i):
        return f"/api/img/{i}" if i else None

    async def items(kind):
        cur = db.items.find({"kind": kind, "active": True}).sort([("sort", 1), ("_id", 1)])
        return [{"id": str(x["_id"]), "title": x.get("title", ""), "link": x.get("link", ""),
                 "image": url(x["image_id"])} async for x in cur]

    on = bool(s.get("offer_enabled"))
    return {"site_name": s.get("site_name", ""), "logo": url(s.get("logo_image_id")),
            "notice": s.get("notice", ""), "banners": await items("banner"),
            "pay": {"bkash": s.get("bkash_number", ""), "nagad": s.get("nagad_number", ""),
                    "bkash_logo": url(s.get("bkash_logo_image_id")), "nagad_logo": url(s.get("nagad_logo_image_id")),
                    "wallet_logo": url(s.get("wallet_logo_image_id"))},
            "offer": {"enabled": on, "title": s.get("offer_title", "Offer"),
                      "items": await items("offer") if on else []},
            "categories": await categories()}


@router.get("/img/{image_id}")
async def image(image_id: str):
    d = await db.images.find_one({"_id": oid(image_id)})
    if not d:
        raise HTTPException(404, "Not found")
    return Response(d["data"], media_type="image/webp",
                    headers={"Cache-Control": "public, max-age=31536000, immutable"})


@router.get("/packages")
async def packages(category: str | None = None):
    """One section page = this list filtered by category (e.g. ?category=weekly_monthly)."""
    cur = db.packages.find({"active": True, **({"category": category} if category else {})}).sort([("category", 1), ("sort", 1), ("price", 1)])
    return [ser(p, drop=("pkg_no", "active", "sort")) async for p in cur]


@router.post("/check-player")
async def check_player(b: PlayerIn, u=Depends(current_user)):
    # Garena has no public lookup API. Plug your upstream reseller's player-name API in here.
    return {"valid": True, "name": None}


@router.post("/orders")
async def create_order(b: OrderIn, u=Depends(current_user)):
    pkg = await db.packages.find_one({"_id": oid(b.package_id), "active": True})
    if not pkg:
        raise HTTPException(404, "Package not available")
    price = pkg["price"]
    if b.method == "wallet":
        hit = await db.users.find_one_and_update(
            {"_id": u["_id"], "balance": {"$gte": price}}, {"$inc": {"balance": -price}})
        if not hit:
            raise HTTPException(402, "Not enough wallet balance")
    else:
        if not b.trx_id:
            raise HTTPException(422, "Transaction ID is required")
        await claim_trx(b.trx_id)
    doc = {"user_id": u["_id"], "pkg_no": pkg["pkg_no"], "category": pkg["category"],
           "title": pkg["title"], "diamonds": pkg["diamonds"], "price": price,
           "player_id": b.player_id, "method": b.method,
           "trx_id": b.trx_id.upper() if b.trx_id else None,
           "payment_received": b.method == "wallet",
           "status": "pending", "created_at": now()}
    try:
        res = await db.orders.insert_one(doc)
    except Exception:
        if b.method == "wallet":
            await db.users.update_one({"_id": u["_id"]}, {"$inc": {"balance": price}})
        else:
            await db.trx_ids.delete_one({"_id": b.trx_id.upper()})
        raise HTTPException(500, "Could not place the order, nothing was charged")
    if b.method == "wallet":
        await ledger(u["_id"], -price, "order", str(res.inserted_id))
    return {"id": str(res.inserted_id), "status": "pending"}


@router.get("/orders")
async def my_orders(u=Depends(current_user)):
    cur = db.orders.find({"user_id": u["_id"]}).sort("created_at", -1).limit(100)
    return [ser(o, drop=("pkg_no", "user_id")) async for o in cur]


@router.post("/wallet/topups")
async def request_topup(b: TopupIn, u=Depends(current_user)):
    await claim_trx(b.trx_id)
    res = await db.topups.insert_one({"user_id": u["_id"], "amount": b.amount,
                                      "method": b.method, "trx_id": b.trx_id.upper(),
                                      "status": "pending", "created_at": now()})
    return {"id": str(res.inserted_id), "status": "pending"}


@router.get("/wallet/topups")
async def my_topups(u=Depends(current_user)):
    cur = db.topups.find({"user_id": u["_id"]}).sort("created_at", -1).limit(100)
    return [ser(t, drop=("user_id",)) async for t in cur]


@router.get("/codes")
async def my_codes(u=Depends(current_user)):
    cur = db.codes.find({"user_id": u["_id"]}).sort("created_at", -1).limit(100)
    return [ser(c, drop=("user_id",)) async for c in cur]
