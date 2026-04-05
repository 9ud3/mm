"""
hold.escrow - Main FastAPI App v2
Discord OAuth2 + bot notifications integrated.
"""

import os, uuid, json, hashlib, hmac, asyncio
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from wallet import WalletService
from escrow import EscrowService
from database import db
from discord_auth import router as discord_auth_router

_bot_module = None

def _get_bot():
    global _bot_module
    if _bot_module is None:
        try:
            import discord_bot
            _bot_module = discord_bot
        except Exception:
            pass
    return _bot_module

@asynccontextmanager
async def lifespan(app):
    bot = _get_bot()
    if bot and os.getenv("DISCORD_BOT_TOKEN"):
        asyncio.create_task(bot.start_bot())
    yield

app = FastAPI(title="HalalMM Escrow API", version="2.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.include_router(discord_auth_router)

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

wallet_svc = WalletService()
escrow_svc = EscrowService(wallet_svc)

async def _notify(event: str, deal: dict):
    bot = _get_bot()
    if not bot:
        return
    try:
        handlers = {"created": bot.notify_deal_created, "funded": bot.notify_deal_funded,
                    "released": bot.notify_deal_released, "disputed": bot.notify_deal_disputed}
        if event in handlers:
            asyncio.create_task(handlers[event](deal))
    except Exception as e:
        print(f"[NOTIFY] {event} failed: {e}")

class CreateDealRequest(BaseModel):
    title: str
    amount: float
    currency: str
    buyer_email: str
    seller_email: str
    release_condition: str
    inspection_days: int = 3
    buyer_discord_id: Optional[str] = None
    seller_discord_id: Optional[str] = None

class FundDealRequest(BaseModel):
    deal_id: str
    tx_hash: str

class ReleaseFundsRequest(BaseModel):
    deal_id: str
    initiator_email: str

class DisputeRequest(BaseModel):
    deal_id: str
    reason: str
    filed_by: str

class RegisterUserRequest(BaseModel):
    email: str
    payout_address: str
    currency: str

@app.post("/deals/create")
async def create_deal(req: CreateDealRequest):
    currency = req.currency.upper()
    if currency not in ["BTC", "LTC", "ETH", "USDT", "USDC"]:
        raise HTTPException(400, f"Unsupported currency: {currency}")
    deal_id = f"ESC-{str(uuid.uuid4())[:8].upper()}"
    escrow_wallet = await wallet_svc.create_escrow_wallet(currency, deal_id)
    deal = {
        "deal_id": deal_id, "title": req.title, "amount": req.amount, "currency": currency,
        "buyer_email": req.buyer_email, "seller_email": req.seller_email,
        "buyer_discord_id": req.buyer_discord_id, "seller_discord_id": req.seller_discord_id,
        "release_condition": req.release_condition, "inspection_days": req.inspection_days,
        "status": "PENDING_FUNDING", "escrow_address": escrow_wallet["address"],
        "escrow_wallet_id": escrow_wallet["wallet_id"], "chain": escrow_wallet["chain"],
        "created_at": datetime.utcnow().isoformat(), "funded_at": None,
        "released_at": None, "tx_in": None, "tx_out": None,
    }
    db.save_deal(deal)
    await _notify("created", deal)
    return {"success": True, "deal_id": deal_id, "deposit_address": escrow_wallet["address"],
            "amount_expected": req.amount, "currency": currency, "status": "PENDING_FUNDING"}

@app.get("/deals/{deal_id}")
async def get_deal(deal_id: str):
    deal = db.get_deal(deal_id)
    if not deal:
        raise HTTPException(404, "Deal not found")
    deal["current_balance"] = await wallet_svc.get_balance(deal["escrow_address"], deal["currency"])
    return deal

@app.get("/deals")
async def list_deals(email: Optional[str] = None, discord_id: Optional[str] = None):
    if discord_id:
        return db.get_deals_by_discord_id(discord_id)
    return db.list_deals(email)

@app.post("/deals/confirm-funding")
async def confirm_funding(req: FundDealRequest):
    deal = db.get_deal(req.deal_id)
    if not deal:
        raise HTTPException(404, "Deal not found")
    if deal["status"] != "PENDING_FUNDING":
        raise HTTPException(400, f"Deal is already {deal['status']}")
    verified = await wallet_svc.verify_transaction(req.tx_hash, deal["escrow_address"], deal["amount"], deal["currency"])
    if not verified["confirmed"]:
        return {"success": False, "message": "Not yet confirmed.", "confirmations": verified.get("confirmations", 0), "required": verified.get("required", 1)}
    db.update_deal(req.deal_id, {"status": "FUNDED", "funded_at": datetime.utcnow().isoformat(), "tx_in": req.tx_hash})
    await _notify("funded", db.get_deal(req.deal_id))
    return {"success": True, "deal_id": req.deal_id, "status": "FUNDED"}

@app.post("/deals/release")
async def release_funds(req: ReleaseFundsRequest):
    deal = db.get_deal(req.deal_id)
    if not deal:
        raise HTTPException(404, "Deal not found")
    if deal["status"] not in ["FUNDED", "IN_PROGRESS"]:
        raise HTTPException(400, f"Cannot release - status is {deal['status']}")
    if req.initiator_email != deal["buyer_email"]:
        raise HTTPException(403, "Only the buyer can release funds")
    seller = db.get_user(deal["seller_email"])
    if not seller or not seller.get("payout_address"):
        raise HTTPException(400, "Seller has not set a payout address")
    tx = await wallet_svc.send_funds(deal["escrow_wallet_id"], seller["payout_address"], deal["amount"], deal["currency"])
    if not tx["success"]:
        raise HTTPException(500, f"Transfer failed: {tx.get('error')}")
    db.update_deal(req.deal_id, {"status": "RELEASED", "released_at": datetime.utcnow().isoformat(), "tx_out": tx["tx_hash"]})
    await _notify("released", db.get_deal(req.deal_id))
    return {"success": True, "deal_id": req.deal_id, "status": "RELEASED", "tx_hash": tx["tx_hash"]}

@app.post("/deals/dispute")
async def raise_dispute(req: DisputeRequest):
    deal = db.get_deal(req.deal_id)
    if not deal:
        raise HTTPException(404, "Deal not found")
    if deal["status"] not in ["FUNDED", "IN_PROGRESS"]:
        raise HTTPException(400, "Can only dispute active funded deals")
    db.update_deal(req.deal_id, {"status": "DISPUTED", "dispute_reason": req.reason, "dispute_filed_by": req.filed_by, "disputed_at": datetime.utcnow().isoformat()})
    await _notify("disputed", db.get_deal(req.deal_id))
    return {"success": True, "deal_id": req.deal_id, "status": "DISPUTED"}

@app.post("/users/register")
async def register_user(req: RegisterUserRequest):
    currency = req.currency.upper()
    if not await wallet_svc.validate_address(req.payout_address, currency):
        raise HTTPException(400, f"Invalid {currency} address")
    db.save_user({"email": req.email, "payout_address": req.payout_address, "currency": currency, "registered_at": datetime.utcnow().isoformat()})
    return {"success": True}

@app.get("/wallet/balance/{currency}/{address}")
async def check_balance(currency: str, address: str):
    balance = await wallet_svc.get_balance(address, currency.upper())
    return {"address": address, "currency": currency.upper(), "balance": balance}

@app.post("/webhooks/tatum")
async def tatum_webhook(request: Request):
    body = await request.body()
    payload = json.loads(body)
    address = payload.get("address")
    amount = float(payload.get("amount", 0))
    tx_hash = payload.get("txId", "")
    deal = db.get_deal_by_address(address)
    if deal and deal["status"] == "PENDING_FUNDING" and amount >= deal["amount"] * 0.999:
        db.update_deal(deal["deal_id"], {"status": "FUNDED", "funded_at": datetime.utcnow().isoformat(), "tx_in": tx_hash})
        await _notify("funded", db.get_deal(deal["deal_id"]))
    return {"received": True}

@app.get("/")
async def root():
    index = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index):
        return FileResponse(index)
    return {"service": "HalalMM Escrow", "version": "2.0.0", "docs": "/docs"}

@app.get("/{full_path:path}")
async def serve_spa(full_path: str):
    index = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index):
        return FileResponse(index)
    raise HTTPException(404, "Not found")

# ── ADMIN ──────────────────────────────────────────────────────────────────────
from fastapi.responses import HTMLResponse

ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")

def check_admin(request: Request):
    auth = request.headers.get("X-Admin-Password", "")
    if auth != ADMIN_PASSWORD:
        raise HTTPException(401, "Unauthorized")

@app.get("/admin/data")
async def admin_list_deals(request: Request):
    check_admin(request)
    return db._read()

@app.patch("/admin/deals/{deal_id}")
async def admin_edit_deal(deal_id: str, request: Request):
    check_admin(request)
    deal = db.get_deal(deal_id)
    if not deal:
        raise HTTPException(404, "Deal not found")
    updates = await request.json()
    db.update_deal(deal_id, updates)
    return db.get_deal(deal_id)

@app.delete("/admin/deals/{deal_id}")
async def admin_delete_deal(deal_id: str, request: Request):
    check_admin(request)
    data = db._read()
    if deal_id not in data["deals"]:
        raise HTTPException(404, "Deal not found")
    del data["deals"][deal_id]
    db._write(data)
    return {"deleted": deal_id}

@app.delete("/admin/users/{discord_id}")
async def admin_delete_user(discord_id: str, request: Request):
    check_admin(request)
    data = db._read()
    if discord_id not in data.get("discord_users", {}):
        raise HTTPException(404, "User not found")
    del data["discord_users"][discord_id]
    db._write(data)
    return {"deleted": discord_id}

@app.get("/admin")
async def admin_page():
    admin_file = os.path.join(STATIC_DIR, "admin.html")
    if os.path.exists(admin_file):
        return FileResponse(admin_file)
    raise HTTPException(404, "Admin page not found")
