"""Request models. Every field is validated server-side; never trust the client."""
from typing import Literal, Optional

from pydantic import BaseModel, Field

Category = Literal["id_code", "weekly_monthly", "weekly_lite", "level_up", "ff_likes"]
TRX = r"^[A-Za-z0-9]{6,20}$"


class RegisterIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=120)
    password: str = Field(min_length=8, max_length=72)


class LoginIn(BaseModel):
    email: str
    password: str


class PlayerIn(BaseModel):
    player_id: str = Field(pattern=r"^\d{5,14}$")


class OrderIn(BaseModel):
    package_id: str
    player_id: str = Field(pattern=r"^\d{5,14}$")
    method: Literal["wallet", "bkash", "nagad"]
    trx_id: Optional[str] = Field(default=None, pattern=TRX)


class TopupIn(BaseModel):
    amount: int = Field(ge=10, le=50000)
    method: Literal["bkash", "nagad"]
    trx_id: str = Field(pattern=TRX)


class PackageIn(BaseModel):
    category: Category
    title: str = Field(min_length=1, max_length=80)
    diamonds: int = Field(ge=0, le=1_000_000)
    price: int = Field(ge=1, le=1_000_000)
    active: bool = True
    sort: int = 0


class PackageUpdate(BaseModel):
    category: Optional[Category] = None
    title: Optional[str] = Field(default=None, min_length=1, max_length=80)
    diamonds: Optional[int] = Field(default=None, ge=0, le=1_000_000)
    price: Optional[int] = Field(default=None, ge=1, le=1_000_000)
    active: Optional[bool] = None
    sort: Optional[int] = None


class CategoryIn(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=60)
    image_id: Optional[str] = Field(default=None, pattern=r"^[0-9a-f]{24}$")


class ImageIn(BaseModel):
    data: str = Field(max_length=6_000_000)  # base64 data URL from the browser


class PriceIn(BaseModel):
    category: Category
    percent: int = Field(ge=-90, le=500)


LINK = r"^(/[A-Za-z0-9_\-./?=&#]*|https://[^\s]+)?$"
IMG = r"^[0-9a-f]{24}$"


class SiteIn(BaseModel):
    site_name: Optional[str] = Field(default=None, min_length=1, max_length=40)
    logo_image_id: Optional[str] = Field(default=None, pattern=IMG)
    notice: Optional[str] = Field(default=None, max_length=300)
    offer_enabled: Optional[bool] = None
    offer_title: Optional[str] = Field(default=None, min_length=1, max_length=40)
    wallet_logo_image_id: Optional[str] = Field(default=None, pattern=IMG)
    bkash_logo_image_id: Optional[str] = Field(default=None, pattern=IMG)
    nagad_logo_image_id: Optional[str] = Field(default=None, pattern=IMG)
    bkash_number: Optional[str] = Field(default=None, pattern=r"^(01\d{9})?$")
    nagad_number: Optional[str] = Field(default=None, pattern=r"^(01\d{9})?$")


class ItemIn(BaseModel):  # a home banner or an offer card
    image_id: str = Field(pattern=IMG)
    title: str = Field(default="", max_length=60)
    link: str = Field(default="", pattern=LINK, max_length=200)
    active: bool = True
    sort: int = 0


class ItemUpdate(BaseModel):
    image_id: Optional[str] = Field(default=None, pattern=IMG)
    title: Optional[str] = Field(default=None, max_length=60)
    link: Optional[str] = Field(default=None, pattern=LINK, max_length=200)
    active: Optional[bool] = None
    sort: Optional[int] = None


class StatusIn(BaseModel):
    status: Literal["completed", "cancelled"]


class BalanceIn(BaseModel):
    amount: int = Field(ge=-1_000_000, le=1_000_000)
    note: str = Field(default="", max_length=120)
