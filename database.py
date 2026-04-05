"""
database.py - Storage layer (JSON for dev, swap for PostgreSQL in production)
Now includes Discord ID indexing and per-user deal statistics.
"""

import json
import os
from typing import Optional
from datetime import datetime

DB_PATH = os.getenv("DB_PATH", "./data/escrow_db.json")


class Database:
    def __init__(self):
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        if not os.path.exists(DB_PATH):
            self._write({"deals": {}, "users": {}, "discord_users": {}})

    def _read(self) -> dict:
        with open(DB_PATH, "r") as f:
            return json.load(f)

    def _write(self, data: dict):
        with open(DB_PATH, "w") as f:
            json.dump(data, f, indent=2)

    def save_deal(self, deal: dict):
        data = self._read()
        data["deals"][deal["deal_id"]] = deal
        self._write(data)

    def get_deal(self, deal_id: str) -> Optional[dict]:
        return self._read()["deals"].get(deal_id)

    def get_deal_by_address(self, address: str) -> Optional[dict]:
        for deal in self._read()["deals"].values():
            if deal.get("escrow_address") == address:
                return deal
        return None

    def update_deal(self, deal_id: str, updates: dict):
        data = self._read()
        if deal_id in data["deals"]:
            data["deals"][deal_id].update(updates)
            self._write(data)

    def list_deals(self, email: Optional[str] = None) -> list:
        deals = list(self._read()["deals"].values())
        if email:
            deals = [d for d in deals if d.get("buyer_email") == email or d.get("seller_email") == email]
        return sorted(deals, key=lambda d: d.get("created_at", ""), reverse=True)

    def get_deals_by_discord_id(self, discord_id: str) -> list:
        deals = list(self._read()["deals"].values())
        return [d for d in deals if d.get("buyer_discord_id") == discord_id or d.get("seller_discord_id") == discord_id]

    def save_user(self, user: dict):
        data = self._read()
        data["users"][user["email"]] = user
        self._write(data)

    def get_user(self, email: str) -> Optional[dict]:
        return self._read()["users"].get(email)

    def save_user_by_discord_id(self, discord_id: str, user: dict):
        data = self._read()
        if "discord_users" not in data:
            data["discord_users"] = {}
        data["discord_users"][discord_id] = user
        if user.get("email"):
            existing = data["users"].get(user["email"], {})
            existing.update({"email": user["email"], "discord_id": discord_id,
                             "discord_username": user.get("discord_username"),
                             "discord_avatar": user.get("discord_avatar")})
            if not existing.get("joined_at"):
                existing["joined_at"] = user.get("joined_at")
            data["users"][user["email"]] = existing
        self._write(data)

    def get_user_by_discord_id(self, discord_id: str) -> Optional[dict]:
        return self._read().get("discord_users", {}).get(discord_id)

    def get_user_deal_stats(self, discord_id: str) -> dict:
        deals = self.get_deals_by_discord_id(discord_id)
        total     = len(deals)
        completed = sum(1 for d in deals if d["status"] == "RELEASED")
        active    = sum(1 for d in deals if d["status"] in ("FUNDED", "IN_PROGRESS", "PENDING_FUNDING"))
        disputed  = sum(1 for d in deals if d["status"] == "DISPUTED")
        volume    = sum(float(d.get("amount", 0)) for d in deals if d.get("currency") in ("USDT", "USDC"))
        durations = []
        for d in deals:
            if d.get("released_at") and d.get("created_at"):
                try:
                    durations.append((datetime.fromisoformat(d["released_at"]) - datetime.fromisoformat(d["created_at"])).total_seconds() / 86400)
                except Exception:
                    pass
        return {
            "total": total, "completed": completed, "active": active,
            "disputed": disputed, "total_usd_volume": round(volume, 2),
            "avg_deal_days": round(sum(durations) / len(durations), 1) if durations else 0.0,
        }


db = Database()
