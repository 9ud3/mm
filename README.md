# HalalMM — Crypto Escrow Service

## Quick Start

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Configure environment
```bash
cp .env.example .env
# Edit .env with your keys
```

### 3. Run the server
```bash
uvicorn main:app --reload --port 8000
```

### 4. Open the app
Visit: http://localhost:8000

---

## Project Structure

```
halalMM/
├── main.py              # FastAPI app + all API routes
├── database.py          # JSON database (swap for PostgreSQL in prod)
├── wallet.py            # Tatum API — wallet creation, balances, transfers
├── escrow.py            # Escrow business logic (fees, release flow)
├── discord_auth.py      # Discord OAuth2 login
├── discord_bot.py       # Discord bot slash commands + notifications
├── requirements.txt
├── .env.example         # Copy to .env and fill in your keys
├── data/                # Auto-created — stores escrow_db.json
└── static/
    └── index.html       # Full frontend (served by FastAPI)
```

---

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | / | Frontend app |
| POST | /deals/create | Create a new escrow deal |
| GET | /deals/{deal_id} | Get deal details |
| GET | /deals?email=... | List deals by email |
| POST | /deals/confirm-funding | Confirm deposit TX |
| POST | /deals/release | Buyer releases funds to seller |
| POST | /deals/dispute | Raise a dispute |
| POST | /users/register | Register payout address |
| GET | /auth/discord | Discord OAuth login |
| GET | /auth/me?token=... | Get current user profile |
| GET | /docs | Interactive API docs (Swagger) |

---

## Dev Mode (no Tatum key needed)

If `TATUM_API_KEY` is not set or is the placeholder value, the app runs in
**mock mode** — wallet addresses are generated deterministically and
transactions are auto-confirmed. Perfect for testing the full flow locally.

---

## Production Checklist

- [ ] Set a real `TATUM_API_KEY` (free tier at tatum.io)
- [ ] Set `TATUM_NETWORK=mainnet`
- [ ] Set a strong `SESSION_SECRET`
- [ ] Configure Discord OAuth2 app with your real domain
- [ ] Set `DISCORD_REDIRECT_URI` to your production URL
- [ ] Replace JSON DB with PostgreSQL (swap `database.py`)
- [ ] Set `ESCROW_PRIVATE_KEY_*` for each currency you support
- [ ] Run behind HTTPS (nginx + certbot)
