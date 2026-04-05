"""
discord_bot.py — hold.escrow Discord Bot
Features:
  - /deal <id>      → look up a deal by ID
  - /mydeals        → list your deals (linked by Discord ID)
  - /profile        → show your escrow profile + stats
  - Sends rich embeds to a #deals channel on deal events
    (created, funded, released, disputed)

Run standalone:  python discord_bot.py
Or start from main.py via asyncio.create_task(start_bot())
"""

import os
import asyncio
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
from database import db

load_dotenv()

BOT_TOKEN      = os.getenv("DISCORD_BOT_TOKEN", "")
DEALS_CHANNEL  = int(os.getenv("DISCORD_DEALS_CHANNEL_ID", "0"))  # #escrow-deals channel
GUILD_ID       = os.getenv("DISCORD_GUILD_ID", "")  # Your server ID

# Brand colors
COLOR_GREEN    = 0x4ade80
COLOR_AMBER    = 0xfbbf24
COLOR_RED      = 0xf87171
COLOR_BLUE     = 0x60a5fa
COLOR_GRAY     = 0x3f3f46


class EscrowBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        if GUILD_ID:
            guild = discord.Object(id=int(GUILD_ID))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()
        print(f"[BOT] Slash commands synced")

    async def on_ready(self):
        print(f"[BOT] Logged in as {self.user} (ID: {self.user.id})")
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="escrow deals"
            )
        )


bot = EscrowBot()


# ─── Slash Commands ───────────────────────────────────────────────────────────

@bot.tree.command(name="deal", description="Look up an escrow deal by ID")
@app_commands.describe(deal_id="The deal ID, e.g. ESC-A1B2C3D4")
async def slash_deal(interaction: discord.Interaction, deal_id: str):
    deal = db.get_deal(deal_id.upper())
    if not deal:
        await interaction.response.send_message(
            embed=_error_embed(f"Deal `{deal_id}` not found."),
            ephemeral=True
        )
        return

    embed = _deal_embed(deal)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="mydeals", description="List all your escrow deals")
async def slash_mydeals(interaction: discord.Interaction):
    discord_id = str(interaction.user.id)
    deals = db.get_deals_by_discord_id(discord_id)

    if not deals:
        await interaction.response.send_message(
            embed=_error_embed("You have no deals linked to your Discord account.\nLogin at the website first to link your account."),
            ephemeral=True
        )
        return

    embed = discord.Embed(
        title="Your Escrow Deals",
        color=COLOR_GREEN,
        description=f"Showing {len(deals)} deal(s) for **{interaction.user.display_name}**"
    )

    for deal in deals[:10]:  # cap at 10 in embed
        status_emoji = _status_emoji(deal["status"])
        embed.add_field(
            name=f"{status_emoji} `{deal['deal_id']}` — {deal['title']}",
            value=(
                f"**{deal['amount']} {deal['currency']}** · "
                f"{deal['status'].replace('_', ' ').title()} · "
                f"{deal['created_at'][:10]}"
            ),
            inline=False
        )

    embed.set_footer(text="hold.escrow · use /deal <id> for full details")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="profile", description="Show your escrow profile and stats")
async def slash_profile(interaction: discord.Interaction):
    discord_id = str(interaction.user.id)
    user = db.get_user_by_discord_id(discord_id)
    stats = db.get_user_deal_stats(discord_id)

    embed = discord.Embed(
        title=f"{interaction.user.display_name}'s Escrow Profile",
        color=COLOR_BLUE,
    )
    embed.set_thumbnail(url=str(interaction.user.display_avatar.url))

    embed.add_field(name="Discord ID", value=f"`{discord_id}`", inline=True)
    embed.add_field(name="Member since", value=user.get("joined_at", "N/A")[:10] if user else "Not registered", inline=True)
    embed.add_field(name="\u200b", value="\u200b", inline=True)

    embed.add_field(name="Total deals",     value=str(stats["total"]),     inline=True)
    embed.add_field(name="Completed",       value=str(stats["completed"]), inline=True)
    embed.add_field(name="Active",          value=str(stats["active"]),    inline=True)

    embed.add_field(name="Total volume",    value=f"${stats['total_usd_volume']:,.2f}", inline=True)
    embed.add_field(name="Avg duration",    value=f"{stats['avg_deal_days']:.1f} days", inline=True)
    embed.add_field(name="Disputes",        value=str(stats["disputed"]),  inline=True)

    if user and user.get("payout_address"):
        addr = user["payout_address"]
        embed.add_field(
            name="Payout address",
            value=f"`{addr[:10]}...{addr[-6:]}`",
            inline=False
        )

    embed.set_footer(text="hold.escrow")
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ─── Event Notifications (called from main.py deal endpoints) ─────────────────

async def notify_deal_created(deal: dict):
    """Post to #deals channel when a new deal is created."""
    channel = bot.get_channel(DEALS_CHANNEL)
    if not channel:
        return

    embed = discord.Embed(
        title="New Escrow Deal Created",
        color=COLOR_BLUE,
        description=f"**{deal['title']}**"
    )
    embed.add_field(name="Deal ID",  value=f"`{deal['deal_id']}`",   inline=True)
    embed.add_field(name="Amount",   value=f"**{deal['amount']} {deal['currency']}**", inline=True)
    embed.add_field(name="Status",   value="Awaiting deposit",        inline=True)
    embed.add_field(name="Buyer",    value=_user_tag(deal, "buyer"),  inline=True)
    embed.add_field(name="Seller",   value=_user_tag(deal, "seller"), inline=True)
    embed.add_field(
        name="Deposit address",
        value=f"`{deal['escrow_address']}`",
        inline=False
    )
    embed.set_footer(text=f"hold.escrow · {deal['created_at'][:10]}")
    await channel.send(embed=embed)


async def notify_deal_funded(deal: dict):
    """Post when funds hit the escrow wallet."""
    channel = bot.get_channel(DEALS_CHANNEL)
    if not channel:
        return

    embed = discord.Embed(
        title="Funds Confirmed in Escrow",
        color=COLOR_GREEN,
        description=f"**{deal['title']}** is now fully funded."
    )
    embed.add_field(name="Deal ID", value=f"`{deal['deal_id']}`", inline=True)
    embed.add_field(name="Amount",  value=f"**{deal['amount']} {deal['currency']}**", inline=True)
    embed.add_field(name="TX In",   value=f"`{deal.get('tx_in','')[:20]}...`", inline=False)
    embed.set_footer(text="Funds locked · seller will be paid on release")

    await channel.send(embed=embed)

    # DM the seller if they have a Discord ID linked
    await _dm_user(deal.get("seller_discord_id"),
        f"Funds have been deposited into escrow for deal **{deal['deal_id']} — {deal['title']}**.\n"
        f"Amount: **{deal['amount']} {deal['currency']}**\n"
        f"Start working — funds will be released when the buyer approves."
    )


async def notify_deal_released(deal: dict):
    """Post when funds are sent to seller."""
    channel = bot.get_channel(DEALS_CHANNEL)
    if not channel:
        return

    embed = discord.Embed(
        title="Funds Released to Seller",
        color=COLOR_GREEN,
        description=f"**{deal['title']}** — deal complete!"
    )
    embed.add_field(name="Deal ID", value=f"`{deal['deal_id']}`", inline=True)
    embed.add_field(name="Amount",  value=f"**{deal['amount']} {deal['currency']}**", inline=True)
    embed.add_field(name="TX Out",  value=f"`{deal.get('tx_out','')[:20]}...`", inline=False)
    embed.set_footer(text="hold.escrow · deal complete")
    await channel.send(embed=embed)

    # DM seller confirmation
    await _dm_user(deal.get("seller_discord_id"),
        f"Payment received! **{deal['amount']} {deal['currency']}** has been sent to your wallet.\n"
        f"Deal: **{deal['deal_id']} — {deal['title']}**\n"
        f"TX: `{deal.get('tx_out','N/A')}`"
    )
    # DM buyer confirmation
    await _dm_user(deal.get("buyer_discord_id"),
        f"Deal **{deal['deal_id']}** is complete. Funds released to the seller."
    )


async def notify_deal_disputed(deal: dict):
    """Post when a dispute is raised."""
    channel = bot.get_channel(DEALS_CHANNEL)
    if not channel:
        return

    embed = discord.Embed(
        title="Dispute Raised",
        color=COLOR_RED,
        description=f"**{deal['title']}** has been disputed. Funds are frozen."
    )
    embed.add_field(name="Deal ID", value=f"`{deal['deal_id']}`", inline=True)
    embed.add_field(name="Filed by", value=deal.get("dispute_filed_by", "Unknown"), inline=True)
    embed.add_field(name="Reason",  value=deal.get("dispute_reason", "No reason given"), inline=False)
    embed.set_footer(text="Admin will review within 24–48 hours")
    await channel.send(embed=embed)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _deal_embed(deal: dict) -> discord.Embed:
    status_colors = {
        "PENDING_FUNDING": COLOR_GRAY,
        "FUNDED":          COLOR_GREEN,
        "IN_PROGRESS":     COLOR_BLUE,
        "RELEASED":        COLOR_GREEN,
        "DISPUTED":        COLOR_RED,
        "REFUNDED":        COLOR_AMBER,
    }
    color = status_colors.get(deal["status"], COLOR_GRAY)
    emoji = _status_emoji(deal["status"])

    embed = discord.Embed(
        title=f"{emoji} {deal['title']}",
        color=color,
        description=f"`{deal['deal_id']}`"
    )
    embed.add_field(name="Amount",    value=f"**{deal['amount']} {deal['currency']}**", inline=True)
    embed.add_field(name="Status",    value=deal["status"].replace("_"," ").title(),    inline=True)
    embed.add_field(name="Created",   value=deal["created_at"][:10],                   inline=True)
    embed.add_field(name="Buyer",     value=_user_tag(deal, "buyer"),                  inline=True)
    embed.add_field(name="Seller",    value=_user_tag(deal, "seller"),                 inline=True)
    if deal.get("tx_out"):
        embed.add_field(name="TX Out", value=f"`{deal['tx_out'][:24]}...`", inline=False)
    embed.add_field(
        name="Condition",
        value=deal.get("release_condition", "N/A"),
        inline=False
    )
    embed.set_footer(text="hold.escrow")
    return embed


def _status_emoji(status: str) -> str:
    return {
        "PENDING_FUNDING": "⏳",
        "FUNDED":          "🔒",
        "IN_PROGRESS":     "🔄",
        "RELEASED":        "✅",
        "DISPUTED":        "⚠️",
        "REFUNDED":        "↩️",
        "EXPIRED":         "🕐",
    }.get(status, "•")


def _user_tag(deal: dict, role: str) -> str:
    discord_id = deal.get(f"{role}_discord_id")
    email      = deal.get(f"{role}_email", "Unknown")
    if discord_id:
        return f"<@{discord_id}>"
    return email


def _error_embed(msg: str) -> discord.Embed:
    return discord.Embed(description=f"❌ {msg}", color=COLOR_RED)


async def _dm_user(discord_id: str | None, message: str):
    """Send a DM to a user by their Discord ID."""
    if not discord_id:
        return
    try:
        user = await bot.fetch_user(int(discord_id))
        await user.send(message)
    except Exception as e:
        print(f"[BOT] DM failed for {discord_id}: {e}")


# ─── Start ────────────────────────────────────────────────────────────────────

async def start_bot():
    """Call this from main.py to run bot alongside FastAPI."""
    if not BOT_TOKEN:
        print("[BOT] DISCORD_BOT_TOKEN not set — bot disabled")
        return
    await bot.start(BOT_TOKEN)


if __name__ == "__main__":
    asyncio.run(bot.start(BOT_TOKEN))
