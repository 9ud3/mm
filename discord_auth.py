"""
discord_auth.py — Discord OAuth2 Login
Flow:
  1. Frontend redirects user to /auth/discord
  2. Discord redirects back to /auth/discord/callback?code=...
  3. We exchange code for access token, fetch user profile
  4. Save discord_id + username to DB, issue our own session token
  5. All deals created by this user are tagged with their discord_id
"""

import os
import time
import hmac
import hashlib
import json
import httpx
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import RedirectResponse
from database import db

router = APIRouter(prefix="/auth", tags=["auth"])

DISCORD_CLIENT_ID     = os.getenv("DISCORD_CLIENT_ID", "")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET", "")
DISCORD_REDIRECT_URI  = os.getenv("DISCORD_REDIRECT_URI", "http://localhost:8000/auth/discord/callback")
SESSION_SECRET        = os.getenv("SESSION_SECRET", "change-me-in-production")

DISCORD_API   = "https://discord.com/api/v10"
DISCORD_OAUTH = "https://discord.com/oauth2/authorize"
DISCORD_TOKEN = "https://discord.com/api/oauth2/token"

SCOPES = "identify email"


# ─── Step 1: Redirect user to Discord login ───────────────────────────────────

@router.get("/discord")
async def discord_login():
    """Redirect browser to Discord OAuth consent screen."""
    url = (
        f"{DISCORD_OAUTH}"
        f"?client_id={DISCORD_CLIENT_ID}"
        f"&redirect_uri={DISCORD_REDIRECT_URI}"
        f"&response_type=code"
        f"&scope={SCOPES.replace(' ', '%20')}"
    )
    return RedirectResponse(url)


# ─── Step 2: Handle callback from Discord ────────────────────────────────────

@router.get("/discord/callback")
async def discord_callback(code: str = Query(...)):
    """
    Discord redirects here with ?code=...
    Exchange code → access token → fetch user profile → save to DB → issue session.
    """
    # Exchange authorization code for access token
    async with httpx.AsyncClient() as client:
        token_resp = await client.post(
            DISCORD_TOKEN,
            data={
                "client_id":     DISCORD_CLIENT_ID,
                "client_secret": DISCORD_CLIENT_SECRET,
                "grant_type":    "authorization_code",
                "code":          code,
                "redirect_uri":  DISCORD_REDIRECT_URI,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

        if token_resp.status_code != 200:
            raise HTTPException(400, f"Discord token exchange failed: {token_resp.text}")

        token_data = token_resp.json()
        access_token = token_data["access_token"]

        # Fetch the user's Discord profile
        user_resp = await client.get(
            f"{DISCORD_API}/users/@me",
            headers={"Authorization": f"Bearer {access_token}"},
        )

        if user_resp.status_code != 200:
            raise HTTPException(400, "Failed to fetch Discord user profile")

        discord_user = user_resp.json()

    discord_id       = discord_user["id"]
    discord_username = discord_user["username"]
    discord_avatar   = discord_user.get("avatar")
    discord_email    = discord_user.get("email", "")

    avatar_url = (
        f"https://cdn.discordapp.com/avatars/{discord_id}/{discord_avatar}.png"
        if discord_avatar else
        f"https://cdn.discordapp.com/embed/avatars/{int(discord_id) % 5}.png"
    )

    # Save or update user in DB
    existing = db.get_user_by_discord_id(discord_id)
    user_record = {
        "discord_id":       discord_id,
        "discord_username": discord_username,
        "discord_avatar":   avatar_url,
        "email":            discord_email,
        "payout_address":   existing.get("payout_address") if existing else None,
        "currency":         existing.get("currency") if existing else None,
        "joined_at":        existing.get("joined_at") if existing else _now(),
        "last_login":       _now(),
    }
    db.save_user_by_discord_id(discord_id, user_record)

    # Issue a simple signed session token
    session_token = _sign_session(discord_id)

    # Redirect to frontend dashboard with token
    frontend_url = os.getenv("FRONTEND_URL", "http://localhost:3000")
    return RedirectResponse(
        f"{frontend_url}/dashboard?token={session_token}&discord_id={discord_id}&username={discord_username}"
    )


# ─── Get current user profile + deal stats ───────────────────────────────────

@router.get("/me")
async def get_me(token: str = Query(...)):
    """
    Returns the logged-in user's profile + deal statistics.
    Pass ?token=<session_token>
    """
    discord_id = _verify_session(token)
    if not discord_id:
        raise HTTPException(401, "Invalid or expired session token")

    user = db.get_user_by_discord_id(discord_id)
    if not user:
        raise HTTPException(404, "User not found")

    # Pull deal stats for this discord_id
    stats = db.get_user_deal_stats(discord_id)

    return {
        "discord_id":       user["discord_id"],
        "username":         user["discord_username"],
        "avatar":           user["discord_avatar"],
        "email":            user.get("email"),
        "payout_address":   user.get("payout_address"),
        "joined_at":        user.get("joined_at"),
        "stats": {
            "total_deals":      stats["total"],
            "completed_deals":  stats["completed"],
            "active_deals":     stats["active"],
            "disputed_deals":   stats["disputed"],
            "total_usd_volume": stats["total_usd_volume"],
            "avg_deal_days":    stats["avg_deal_days"],
        },
    }


# ─── Session token (simple HMAC — swap for JWT in production) ─────────────────

def _sign_session(discord_id: str) -> str:
    """Create a signed token: discord_id:timestamp:hmac"""
    ts      = str(int(time.time()))
    payload = f"{discord_id}:{ts}"
    sig     = hmac.new(SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()[:16]
    return f"{payload}:{sig}"


def _verify_session(token: str) -> str | None:
    """Verify and return discord_id, or None if invalid/expired."""
    try:
        parts = token.split(":")
        if len(parts) != 3:
            return None
        discord_id, ts, sig = parts
        age = int(time.time()) - int(ts)
        if age > 60 * 60 * 24 * 30:  # 30-day expiry
            return None
        payload  = f"{discord_id}:{ts}"
        expected = hmac.new(SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()[:16]
        return discord_id if hmac.compare_digest(sig, expected) else None
    except Exception:
        return None


def _now():
    from datetime import datetime
    return datetime.utcnow().isoformat()
