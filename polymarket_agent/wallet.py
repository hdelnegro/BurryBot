"""
wallet.py — Wallet adapter abstraction for Polymarket live trading.

Each adapter provides an authenticated ClobClient instance.
Supports Magic Link (Polymarket.com email accounts) out of the box;
EOA and Gnosis Safe are stubbed for future use.

Usage:
    from wallet import wallet_from_env
    wallet = wallet_from_env()           # reads POLY_WALLET_TYPE from .env
    clob_client = wallet.build_clob_client()

Environment variables (see .env.example):
    POLY_WALLET_TYPE    = magic | eoa | gnosis
    POLY_PRIVATE_KEY    = 0x...   (proxy signing key from Polymarket account settings)
    POLY_FUNDER_ADDRESS = 0x...   (your Polymarket wallet address that holds USDC)
"""

import os
from abc import ABC, abstractmethod


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------

class WalletAdapter(ABC):
    """
    Abstract base for wallet adapters.
    Subclasses provide an authenticated ClobClient and expose the funder address.
    """

    @abstractmethod
    def build_clob_client(self):
        """Return a fully authenticated ClobClient ready to place orders."""
        ...

    @property
    @abstractmethod
    def funder_address(self) -> str:
        """Return the wallet address that holds USDC (the 'funder' in Polymarket's API)."""
        ...


# ---------------------------------------------------------------------------
# Magic Link wallet (Polymarket.com email account)
# ---------------------------------------------------------------------------

class MagicLinkWallet(WalletAdapter):
    """
    Polymarket.com email-based account (signature_type=1).

    Key properties:
    - Uses the proxy signing key exported from account settings (not the main wallet key)
    - Gasless relayer — no POL needed for gas
    - Contract approvals are pre-handled by Polymarket — no set_allowances() call needed
    - L2 API credentials are derived on the fly from the private key each startup

    Args:
        private_key:     Proxy signing key (0x-prefixed hex) from Polymarket account settings
        funder_address:  Your Polymarket wallet address (holds USDC.e on Polygon)
    """

    def __init__(self, private_key: str, funder_address: str):
        self._private_key    = private_key
        self._funder_address = funder_address

    @property
    def funder_address(self) -> str:
        return self._funder_address

    def build_clob_client(self):
        """
        Build and return an authenticated ClobClient for a Magic Link account.

        Steps:
        1. Instantiate ClobClient with signature_type=1 and funder address
        2. Derive (or fetch) L2 API credentials (apiKey + secret + passphrase)
        3. Set those credentials so subsequent API calls are signed
        """
        from py_clob_client.client import ClobClient
        from config import CLOB_HOST, POLY_CHAIN_ID

        client = ClobClient(
            host           = CLOB_HOST,
            key            = self._private_key,
            chain_id       = POLY_CHAIN_ID,
            signature_type = 1,           # Magic Link
            funder         = self._funder_address,
        )
        creds = client.create_or_derive_api_credentials()
        client.set_api_creds(creds)
        return client


# ---------------------------------------------------------------------------
# EOA wallet (standard private key / MetaMask / hardware wallet)
# ---------------------------------------------------------------------------

class EOAWallet(WalletAdapter):
    """
    Standard EOA (Externally Owned Account) wallet — signature_type=0.

    Unlike Magic Link:
    - Requires POL for gas on Polygon
    - Requires one-time contract approvals via client.set_allowances() before first trade

    NOT YET IMPLEMENTED — stub only.  Add when needed.
    """

    def __init__(self, private_key: str):
        self._private_key = private_key

    @property
    def funder_address(self) -> str:
        raise NotImplementedError("EOAWallet.funder_address not yet implemented")

    def build_clob_client(self):
        raise NotImplementedError(
            "EOAWallet not yet fully implemented.\n"
            "Steps needed:\n"
            "  1. Derive funder_address from private key\n"
            "  2. Call client.set_allowances() for one-time contract approval\n"
            "  3. Ensure POL is available for gas"
        )


# ---------------------------------------------------------------------------
# Gnosis Safe (multi-sig)
# ---------------------------------------------------------------------------

class GnosisSafeWallet(WalletAdapter):
    """
    Multi-sig wallet via Gnosis Safe — signature_type=2.

    NOT YET IMPLEMENTED — stub only.  Add when needed.
    """

    @property
    def funder_address(self) -> str:
        raise NotImplementedError("GnosisSafeWallet not yet implemented")

    def build_clob_client(self):
        raise NotImplementedError("GnosisSafeWallet not yet implemented")


# ---------------------------------------------------------------------------
# Factory: build from environment
# ---------------------------------------------------------------------------

def wallet_from_env() -> WalletAdapter:
    """
    Read wallet configuration from environment variables (via .env) and
    return the appropriate WalletAdapter subclass.

    Required variables:
        POLY_WALLET_TYPE    = magic | eoa | gnosis  (default: magic)
        POLY_PRIVATE_KEY    = 0x...
        POLY_FUNDER_ADDRESS = 0x...  (required for magic; derived for eoa in future)

    Loads .env automatically if present.
    """
    from dotenv import load_dotenv
    load_dotenv()

    wallet_type = os.environ.get("POLY_WALLET_TYPE", "magic").lower().strip()

    if wallet_type == "magic":
        private_key = os.environ.get("POLY_PRIVATE_KEY")
        funder      = os.environ.get("POLY_FUNDER_ADDRESS")

        if not private_key:
            raise ValueError(
                "POLY_PRIVATE_KEY not set in environment.\n"
                "Export your proxy signing key from Polymarket account settings → .env"
            )
        if not funder:
            raise ValueError(
                "POLY_FUNDER_ADDRESS not set in environment.\n"
                "Set this to your Polymarket wallet address in .env"
            )

        return MagicLinkWallet(private_key=private_key, funder_address=funder)

    elif wallet_type == "eoa":
        private_key = os.environ.get("POLY_PRIVATE_KEY")
        if not private_key:
            raise ValueError("POLY_PRIVATE_KEY not set in environment.")
        return EOAWallet(private_key=private_key)

    elif wallet_type == "gnosis":
        return GnosisSafeWallet()

    else:
        raise ValueError(
            f"Unknown POLY_WALLET_TYPE: '{wallet_type}'\n"
            f"Valid options: magic, eoa, gnosis"
        )
