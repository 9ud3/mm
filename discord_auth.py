"""
discord_auth.py - Discord OAuth2 Login
Automatically adds users to the Discord server on login.
"""

import os
import time
import hmac
import hashlib
import httpx
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import RedirectResponse
from database import db

router = APIRouter(prefix="/auth", tags=["auth"])

DISCORD_CLIENT_ID     = os.getenv("DISCORD_CLIENT_ID", "")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET", "")
DISCORD_REDIRECT_URI  = os.getenv("DISCORD_REDIRECT_URI", "http://localhost:8000/auth/discord/callback")
SESSION_SECRET        = os.getenv("SESSION_SECRET", "change-me-in-production")
DISCORD_BOT_TOKEN     = os.getenv("DISCORD_BOT_TOKEN", "")
DISCORD_GUILD_ID      = os.getenv("DISCORD_GUILD_ID", "")

DISCORD_API   = "https://discord.com/api/v10"
DISCORD_OAUTH = "https://discord.com/oauth2/authorize"
DISCORD_TOKEN = "https://discord.com/api/oauth2/token"

SCOPES = "identify email guilds.join"


@router.get("/discord")
async def discord_login():
    url = (
        f"{DISCORD_OAUTH}"
        f"?client_id={DISCORD_CLIENT_ID}"
        f"&redirect_uri={DISCORD_REDIRECT_URI}"
        f"&response_type=code"
        f"&scope={SCOPES.replace(' ', '%20')}"
    )
    return RedirectResponse(url)


@router.get("/discord/callback")
async def discord_callback(code: str = Query(...)):
    async with httpx.AsyncClient() as client:
        # Exchange code for token
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

        # Fetch user profile
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

        # Auto-add user to Discord server
        if DISCORD_GUILD_ID and DISCORD_BOT_TOKEN:
            try:
                await client.put(
                    f"{DISCORD_API}/guilds/{DISCORD_GUILD_ID}/members/{discord_id}",
                    headers={
                        "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
                        "Content-Type": "application/json",
                    },
                    json={"access_token": access_token},
                )
                print(f"[AUTH] Added {discord_username} to guild {DISCORD_GUILD_ID}")
            except Exception as e:
                print(f"[AUTH] Failed to add user to guild: {e}")

    # Save user to DB
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

    session_token = _sign_session(discord_id)
    frontend_url = os.getenv("FRONTEND_URL", "http://localhost:3000")
    return RedirectResponse(
        f"{frontend_url}/dashboard?token={session_token}&discord_id={discord_id}&username={discord_username}"
    )


@router.get("/me")
async def get_me(token: str = Query(...)):
    discord_id = _verify_session(token)
    if not discord_id:
        raise HTTPException(401, "Invalid or expired session token")

    user = db.get_user_by_discord_id(discord_id)
    if not user:
        raise HTTPException(404, "User not found")

    stats = db.get_user_deal_stats(discord_id)

    return {
        "discord_id":     user["discord_id"],
        "username":       user["discord_username"],
        "avatar":         user["discord_avatar"],
        "email":          user.get("email"),
        "payout_address": user.get("payout_address"),
        "joined_at":      user.get("joined_at"),
        "stats": {
            "total_deals":      stats["total"],
            "completed_deals":  stats["completed"],
            "active_deals":     stats["active"],
            "disputed_deals":   stats["disputed"],
            "total_usd_volume": stats["total_usd_volume"],
            "avg_deal_days":    stats["avg_deal_days"],
        },
    }


def _sign_session(discord_id: str) -> str:
    ts      = str(int(time.time()))
    payload = f"{discord_id}:{ts}"
    sig     = hmac.new(SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()[:16]
    return f"{payload}:{sig}"


def _verify_session(token: str) -> str | None:
    try:
        parts = token.split(":")
        if len(parts) != 3:
            return None
        discord_id, ts, sig = parts
        if int(time.time()) - int(ts) > 60 * 60 * 24 * 30:
            return None
        payload  = f"{discord_id}:{ts}"
        expected = hmac.new(SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()[:16]
        return discord_id if hmac.compare_digest(sig, expected) else None
    except Exception:
        return None


def _now():
    from datetime import datetime
    return datetime.utcnow().isoformat()
