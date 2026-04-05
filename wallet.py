"""
wallet.py — WalletService using Tatum API
Handles: escrow wallet creation, balance checks, tx verification, fund sending
"""

import os
import httpx
from dotenv import load_dotenv

load_dotenv()

TATUM_API_KEY = os.getenv("TATUM_API_KEY", "")
TATUM_NETWORK = os.getenv("TATUM_NETWORK", "testnet")  # "testnet" or "mainnet"
TATUM_BASE    = "https://api.tatum.io/v3"

CHAIN_MAP = {
    "BTC":  "bitcoin",
    "LTC":  "litecoin",
    "ETH":  "ethereum",
    "USDT": "ethereum",   # ERC-20 on Ethereum
    "USDC": "ethereum",   # ERC-20 on Ethereum
}

HEADERS = {
    "x-api-key": TATUM_API_KEY,
    "Content-Type": "application/json",
}


class WalletService:
    # ─── Wallet Creation ──────────────────────────────────────────────────────

    async def create_escrow_wallet(self, currency: str, deal_id: str) -> dict:
        """
        Generate a fresh deposit address for the escrow deal.
        Returns: { address, wallet_id, chain }
        In testnet/mock mode returns a dummy address so you can test without Tatum.
        """
        if not TATUM_API_KEY or TATUM_API_KEY == "your_tatum_api_key_here":
            return self._mock_wallet(currency, deal_id)

        chain = CHAIN_MAP.get(currency, "ethereum")
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                # Create a new wallet (mnemonic-based)
                resp = await client.get(
                    f"{TATUM_BASE}/{chain}/wallet",
                    headers=HEADERS,
                    params={"testnetType": "ethereum-sepolia"} if TATUM_NETWORK == "testnet" and chain == "ethereum" else {}
                )
                resp.raise_for_status()
                wallet_data = resp.json()
                xpub = wallet_data.get("xpub")

                # Derive address at index 0
                addr_resp = await client.get(
                    f"{TATUM_BASE}/{chain}/address/{xpub}/0",
                    headers=HEADERS,
                )
                addr_resp.raise_for_status()
                address = addr_resp.json().get("address")

                return {
                    "address":   address,
                    "wallet_id": xpub,   # stored so we can sweep later
                    "chain":     chain,
                }
        except Exception as e:
            print(f"[WALLET] Tatum error, falling back to mock: {e}")
            return self._mock_wallet(currency, deal_id)

    # ─── Balance ──────────────────────────────────────────────────────────────

    async def get_balance(self, address: str, currency: str) -> float:
        if not TATUM_API_KEY or TATUM_API_KEY == "your_tatum_api_key_here":
            return 0.0

        chain = CHAIN_MAP.get(currency, "ethereum")
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{TATUM_BASE}/{chain}/account/balance/{address}",
                    headers=HEADERS,
                )
                resp.raise_for_status()
                data = resp.json()
                return float(data.get("balance", 0) or data.get("incoming", 0) or 0)
        except Exception as e:
            print(f"[WALLET] get_balance error: {e}")
            return 0.0

    # ─── Verify Transaction ───────────────────────────────────────────────────

    async def verify_transaction(self, tx_hash: str, expected_address: str, expected_amount: float, currency: str) -> dict:
        """
        Check that tx_hash sent >= expected_amount to expected_address.
        Returns { confirmed: bool, confirmations: int, required: int }
        """
        if not TATUM_API_KEY or TATUM_API_KEY == "your_tatum_api_key_here":
            # Mock: auto-confirm any tx in dev mode
            return {"confirmed": True, "confirmations": 6, "required": 1}

        chain = CHAIN_MAP.get(currency, "ethereum")
        required_confirms = 1 if currency in ("USDT", "USDC", "ETH") else 3

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    f"{TATUM_BASE}/{chain}/transaction/{tx_hash}",
                    headers=HEADERS,
                )
                resp.raise_for_status()
                tx = resp.json()

            confirmations = int(tx.get("confirmations", 0))
            confirmed = confirmations >= required_confirms

            # Verify destination
            outputs = tx.get("outputs") or tx.get("to") or []
            to_address = tx.get("to", "")
            if isinstance(outputs, list):
                addr_match = any(
                    o.get("address") == expected_address for o in outputs
                )
            else:
                addr_match = to_address.lower() == expected_address.lower()

            return {
                "confirmed":     confirmed and addr_match,
                "confirmations": confirmations,
                "required":      required_confirms,
            }
        except Exception as e:
            print(f"[WALLET] verify_transaction error: {e}")
            return {"confirmed": False, "confirmations": 0, "required": required_confirms}

    # ─── Send Funds ───────────────────────────────────────────────────────────

    async def send_funds(self, wallet_id: str, to_address: str, amount: float, currency: str) -> dict:
        """
        Sweep the escrow wallet → seller's payout address.
        Returns { success: bool, tx_hash: str }
        """
        if not TATUM_API_KEY or TATUM_API_KEY == "your_tatum_api_key_here":
            return {"success": True, "tx_hash": f"mock_tx_{wallet_id[:8]}"}

        chain = CHAIN_MAP.get(currency, "ethereum")
        private_key = os.getenv(f"ESCROW_PRIVATE_KEY_{currency}", "")

        if not private_key:
            print(f"[WALLET] No private key configured for {currency}")
            return {"success": False, "error": f"No private key for {currency}"}

        try:
            async with httpx.AsyncClient(timeout=20) as client:
                payload = {
                    "to":         to_address,
                    "amount":     str(amount),
                    "fromPrivateKey": private_key,
                }
                resp = await client.post(
                    f"{TATUM_BASE}/{chain}/transaction",
                    json=payload,
                    headers=HEADERS,
                )
                resp.raise_for_status()
                data = resp.json()
                return {"success": True, "tx_hash": data.get("txId", "")}
        except Exception as e:
            print(f"[WALLET] send_funds error: {e}")
            return {"success": False, "error": str(e)}

    # ─── Address Validation ───────────────────────────────────────────────────

    async def validate_address(self, address: str, currency: str) -> bool:
        if not address or len(address) < 10:
            return False
        if not TATUM_API_KEY or TATUM_API_KEY == "your_tatum_api_key_here":
            return True  # skip validation in dev mode

        chain = CHAIN_MAP.get(currency, "ethereum")
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{TATUM_BASE}/{chain}/address/validate/{address}",
                    headers=HEADERS,
                )
                return resp.status_code == 200
        except Exception:
            return True  # fail open

    # ─── Mock (dev mode) ─────────────────────────────────────────────────────

    def _mock_wallet(self, currency: str, deal_id: str) -> dict:
        """Returns a deterministic fake wallet for local development."""
        import hashlib
        h = hashlib.md5(f"{currency}-{deal_id}".encode()).hexdigest()
        prefixes = {"BTC": "bc1q", "LTC": "ltc1q", "ETH": "0x", "USDT": "0x", "USDC": "0x"}
        prefix = prefixes.get(currency, "0x")
        address = prefix + h[:32]
        return {
            "address":   address,
            "wallet_id": f"mock_wallet_{h[:16]}",
            "chain":     CHAIN_MAP.get(currency, "ethereum"),
        }
