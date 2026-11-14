"""Admin API. Packages are addressed by their internal package number (pkg_no)."""
import base64
import io

from fastapi import APIRouter, Depends, HTTPException
from PIL import Image
from pymongo import ReturnDocument

from .core import CATEGORIES, admin_user, category_list, db, ledger, now, oid, ser
from .schemas import (BalanceIn, CategoryIn, ImageIn, ItemIn, ItemUpdate, PackageIn,
                      PackageUpdate, PriceIn, SiteIn, StatusIn)

router = APIRouter(prefix="/api/admin", dependencies=[Depends(admin_user)])


async def with_emails(rows: list) -> list:
    ids = list({r["user_id"] for r in rows})
    emails = {u["_id"]: u["email"] async for u in db.users.find({"_id": {"$in": ids}})}
    return [{**ser(r, drop=("user_id",)), "user_email": emails.get(r["user_id"], "?")} for r in rows]


# ---- Packages -------------------------------------------------------------
@router.get("/packages")
async def list_packages():
    return [ser(p) async for p in db.packages.find().sort("pkg_no", 1)]


@router.post("/packages")
async def add_package(b: PackageIn):
    c = await db.counters.find_one_and_update(
        {"_id": "pkg"}, {"$inc": {"seq": 1}}, upsert=True, return_document=ReturnDocument.AFTER)
    doc = {**b.model_dump(), "pkg_no": c["seq"], "created_at": now()}
    res = await db.packages.insert_one(doc)
    return ser({**doc, "_id": res.inserted_id})


@router.patch("/packages/{pkg_no}")
async def edit_package(pkg_no: int, b: PackageUpdate):
    changes = b.model_dump(exclude_none=True)
    if not changes:
        raise HTTPException(422, "Nothing to update")
    p = await db.packages.find_one_and_update(
        {"pkg_no": pkg_no}, {"$set": changes}, return_document=ReturnDocument.AFTER)
    if not p:
        raise HTTPException(404, "Package not found")
    return ser(p)


@router.delete("/packages/{pkg_no}")
async def delete_package(pkg_no: int):
    # Past orders keep their own snapshot (title/diamonds/price), so deleting is safe.
    if not (await db.packages.delete_one({"pkg_no": pkg_no})).deleted_count:
        raise HTTPException(404, "Package not found")
    return {"ok": True}


# ---- Orders ---------------------------------------------------------------
@router.get("/orders")
async def list_orders(status: str | None = None):
    q = {"status": status} if status in ("pending", "completed", "cancelled") else {}
    rows = [o async for o in db.orders.find(q).sort("created_at", -1).limit(200)]
    return await with_emails(rows)


@router.post("/orders/{order_id}/status")
async def set_order_status(order_id: str, b: StatusIn):
    # The status filter makes this atomic: an order can only leave "pending" once.
    o = await db.orders.find_one_and_update(
        {"_id": oid(order_id), "status": "pending"},
        {"$set": {"status": b.status, "updated_at": now()}})
    if not o:
        raise HTTPException(409, "Order not found or already handled")
    if b.status == "completed":
        await db.users.update_one({"_id": o["user_id"]}, {"$inc": {"total_spent": o["price"]}})
    elif o["method"] == "wallet":
        await db.users.update_one({"_id": o["user_id"]}, {"$inc": {"balance": o["price"]}})
        await ledger(o["user_id"], o["price"], "order_refund", order_id)
    return {"ok": True}


# ---- Users & wallet -------------------------------------------------------
@router.get("/users")
async def list_users():
    return [ser(u, drop=("pw_hash",)) async for u in db.users.find().sort("created_at", -1).limit(500)]


@router.post("/users/{user_id}/balance")
async def adjust_balance(user_id: str, b: BalanceIn):
    q = {"_id": oid(user_id)}
    if b.amount < 0:
        q["balance"] = {"$gte": -b.amount}
    u = await db.users.find_one_and_update(q, {"$inc": {"balance": b.amount}})
    if not u:
        raise HTTPException(409, "User not found or balance too low")
    await ledger(u["_id"], b.amount, "admin_adjust:" + b.note)
    return {"ok": True}


@router.get("/topups")
async def list_topups(status: str | None = None):
    q = {"status": status} if status in ("pending", "approved", "rejected") else {}
    rows = [t async for t in db.topups.find(q).sort("created_at", -1).limit(200)]
    return await with_emails(rows)


@router.post("/topups/{topup_id}/approve")
async def approve_topup(topup_id: str):
    t = await db.topups.find_one_and_update(
        {"_id": oid(topup_id), "status": "pending"},
        {"$set": {"status": "approved", "updated_at": now()}})
    if not t:
        raise HTTPException(409, "Request not found or already handled")
    await db.users.update_one({"_id": t["user_id"]}, {"$inc": {"balance": t["amount"]}})
    await ledger(t["user_id"], t["amount"], "topup", topup_id)
    return {"ok": True}


@router.post("/topups/{topup_id}/reject")
async def reject_topup(topup_id: str):
    t = await db.topups.find_one_and_update(
        {"_id": oid(topup_id), "status": "pending"},
        {"$set": {"status": "rejected", "updated_at": now()}})
    if not t:
        raise HTTPException(409, "Request not found or already handled")
    await db.trx_ids.delete_one({"_id": t["trx_id"]})
    return {"ok": True}


# ---- Sections (categories) and images ---------------------------------------
Image.MAX_IMAGE_PIXELS = 30_000_000


@router.get("/categories")
async def list_categories():
    return await category_list()


@router.post("/images")
async def upload_image(b: ImageIn):
    """Re-encodes every upload as a <=1000px WebP, so pages stay fast and files are safe."""
    try:
        raw = base64.b64decode(b.data.split(",")[-1])
        if len(raw) > 4_000_000:
            raise ValueError
        im = Image.open(io.BytesIO(raw))
        im = im.convert("RGBA" if (im.mode in ("RGBA", "LA") or "transparency" in im.info) else "RGB")
        im.thumbnail((1000, 1000))
        buf = io.BytesIO()
        im.save(buf, "WEBP", quality=80)
    except Exception:
        raise HTTPException(422, "Not a valid image (max 4 MB)")
    res = await db.images.insert_one({"data": buf.getvalue(), "created_at": now()})
    return {"id": str(res.inserted_id)}


@router.put("/categories/{key}")
async def set_category(key: str, b: CategoryIn):
    changes = b.model_dump(exclude_none=True)
    if key not in CATEGORIES or not changes:
        raise HTTPException(422, "Nothing to update")
    if "image_id" in changes and not await db.images.find_one({"_id": oid(changes["image_id"])}, {"_id": 1}):
        raise HTTPException(422, "Unknown image")
    await db.categories.update_one({"_id": key}, {"$set": changes}, upsert=True)
    return {"ok": True}


@router.post("/packages/bulk-price")
async def bulk_price(b: PriceIn):
    """Raise or lower every price in one section by a percentage (rounded, minimum 1)."""
    f = 1 + b.percent / 100
    r = await db.packages.update_many(
        {"category": b.category},
        [{"$set": {"price": {"$max": [1, {"$round": [{"$multiply": ["$price", f]}, 0]}]}}}])
    return {"changed": r.modified_count}


# ---- Transactions (payments reported by customers) -----------------------------
@router.get("/transactions")
async def transactions(q: str = ""):
    f = {"trx_id": q.strip().upper()} if q.strip() else {}
    rows = []
    async for t in db.topups.find(f).sort("created_at", -1).limit(200):
        rows.append({"_id": t["_id"], "kind": "add_money", "created_at": t["created_at"],
                     "user_id": t["user_id"], "method": t["method"], "trx_id": t["trx_id"],
                     "amount": t["amount"], "status": t["status"],
                     "received": t["status"] == "approved"})
    async for o in db.orders.find({**f, "method": {"$ne": "wallet"}}).sort("created_at", -1).limit(200):
        rows.append({"_id": o["_id"], "kind": "order", "created_at": o["created_at"],
                     "user_id": o["user_id"], "method": o["method"], "trx_id": o["trx_id"],
                     "amount": o["price"], "status": o["status"], "title": o["title"],
                     "received": o.get("payment_received", False)})
    rows.sort(key=lambda r: r["created_at"], reverse=True)
    return await with_emails(rows[:300])


@router.get("/transactions/check/{trx}")
async def check_trx(trx: str):
    trx = trx.strip().upper()
    t = await db.topups.find_one({"trx_id": trx})
    o = await db.orders.find_one({"trx_id": trx})
    return {"claimed": bool(await db.trx_ids.find_one({"_id": trx})),
            "in": "add_money" if t else ("order" if o else None), "status": (t or o or {}).get("status")}


@router.post("/orders/{order_id}/payment-received")
async def payment_received(order_id: str):
    r = await db.orders.update_one(
        {"_id": oid(order_id), "status": "pending", "method": {"$ne": "wallet"}},
        {"$set": {"payment_received": True}})
    if not r.matched_count:
        raise HTTPException(409, "Order not found or already handled")
    return {"ok": True}


# ---- Site identity, home banners, offer section ------------------------------
KINDS = ("banner", "offer")
LOGO_KEYS = ("logo_image_id", "bkash_logo_image_id", "nagad_logo_image_id", "wallet_logo_image_id")


async def need_image(image_id: str):
    if not await db.images.find_one({"_id": oid(image_id)}, {"_id": 1}):
        raise HTTPException(422, "Unknown image")


async def drop_image(image_id):
    if image_id:
        await db.images.delete_one({"_id": oid(image_id)})


@router.get("/site")
async def get_site():
    s = await db.settings.find_one({"_id": "site"}) or {}
    return {"site_name": s.get("site_name", ""), "logo_image_id": s.get("logo_image_id"),
            "notice": s.get("notice", ""), "offer_enabled": bool(s.get("offer_enabled")),
            "offer_title": s.get("offer_title", "Offer"),
            "bkash_number": s.get("bkash_number", ""), "nagad_number": s.get("nagad_number", ""),
            "bkash_logo_image_id": s.get("bkash_logo_image_id"), "nagad_logo_image_id": s.get("nagad_logo_image_id"),
            "wallet_logo_image_id": s.get("wallet_logo_image_id")}


@router.put("/site")
async def set_site(b: SiteIn):
    ch = b.model_dump(exclude_none=True)
    if not ch:
        raise HTTPException(422, "Nothing to update")
    keys = [k for k in LOGO_KEYS if k in ch]
    cur = await db.settings.find_one({"_id": "site"}) or {}
    for k in keys:
        await need_image(ch[k])
    await db.settings.update_one({"_id": "site"}, {"$set": ch}, upsert=True)
    for k in keys:
        if cur.get(k) and cur[k] != ch[k]:
            await drop_image(cur[k])
    return {"ok": True}


def check_kind(kind: str):
    if kind not in KINDS:
        raise HTTPException(404, "Not found")


@router.get("/items/{kind}")
async def list_items(kind: str):
    check_kind(kind)
    cur = db.items.find({"kind": kind}).sort([("sort", 1), ("_id", 1)])
    return [ser(x, drop=("kind",)) async for x in cur]


@router.post("/items/{kind}")
async def add_item(kind: str, b: ItemIn):
    check_kind(kind)
    await need_image(b.image_id)
    doc = {**b.model_dump(), "kind": kind, "created_at": now()}
    await db.items.insert_one(doc)
    return ser(doc, drop=("kind",))


@router.patch("/items/{kind}/{item_id}")
async def edit_item(kind: str, item_id: str, b: ItemUpdate):
    check_kind(kind)
    ch = b.model_dump(exclude_none=True)
    if not ch:
        raise HTTPException(422, "Nothing to update")
    if "image_id" in ch:
        await need_image(ch["image_id"])
    old = await db.items.find_one_and_update({"_id": oid(item_id), "kind": kind}, {"$set": ch})
    if not old:
        raise HTTPException(404, "Not found")
    if "image_id" in ch and old.get("image_id") != ch["image_id"]:
        await drop_image(old.get("image_id"))
    return {"ok": True}


@router.delete("/items/{kind}/{item_id}")
async def delete_item(kind: str, item_id: str):
    check_kind(kind)
    old = await db.items.find_one_and_delete({"_id": oid(item_id), "kind": kind})
    if not old:
        raise HTTPException(404, "Not found")
    await drop_image(old.get("image_id"))
    return {"ok": True}
