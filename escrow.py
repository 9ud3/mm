"""
escrow.py — EscrowService
Thin orchestration layer between API routes and WalletService.
Keeps business rules (fee calculation, state transitions) in one place.
"""

from wallet import WalletService

PLATFORM_FEE_PERCENT = 0.01   # 1% fee on release (set to 0 to disable)
FEE_WALLET = {
    "BTC":  "bc1qfee000halalmmplatformwallet000btc",
    "ETH":  "0xFee000HalalMMPlatformWallet000ETH",
    "LTC":  "ltc1qfee000halalmmplatformwallet000ltc",
    "USDT": "0xFee000HalalMMPlatformWallet000USDT",
    "USDC": "0xFee000HalalMMPlatformWallet000USDC",
}


class EscrowService:
    def __init__(self, wallet_svc: WalletService):
        self.wallet = wallet_svc

    def calculate_fee(self, amount: float) -> float:
        """Returns the platform fee for a given deal amount."""
        return round(amount * PLATFORM_FEE_PERCENT, 8)

    def seller_receives(self, amount: float) -> float:
        """Amount the seller receives after fee deduction."""
        return round(amount - self.calculate_fee(amount), 8)

    async def release(self, deal: dict) -> dict:
        """
        Release funds from escrow wallet → seller's payout address.
        Optionally takes a platform fee.
        Returns { success, tx_hash, fee_tx_hash? }
        """
        amount   = float(deal["amount"])
        currency = deal["currency"]
        wallet_id = deal["escrow_wallet_id"]

        from database import db
        seller = db.get_user(deal["seller_email"])
        if not seller or not seller.get("payout_address"):
            return {"success": False, "error": "Seller has no payout address"}

        seller_amount = self.seller_receives(amount)
        fee_amount    = self.calculate_fee(amount)

        # Send to seller
        result = await self.wallet.send_funds(
            wallet_id, seller["payout_address"], seller_amount, currency
        )
        if not result["success"]:
            return result

        # Optionally send fee
        fee_tx = None
        if fee_amount > 0 and FEE_WALLET.get(currency):
            fee_result = await self.wallet.send_funds(
                wallet_id, FEE_WALLET[currency], fee_amount, currency
            )
            fee_tx = fee_result.get("tx_hash")

        return {
            "success":      True,
            "tx_hash":      result["tx_hash"],
            "fee_tx_hash":  fee_tx,
            "seller_got":   seller_amount,
            "fee_taken":    fee_amount,
        }
