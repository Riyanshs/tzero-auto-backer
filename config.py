"""Shared configuration for the tzero liftoff-circle backer.

All secrets come from the environment — never from a file in this repo.
Required:
  TZERO_PRIVATE_KEY  64-hex private key of the buyer wallet (no 0x prefix),
                     OR TZERO_KEY_FILE pointing at a JSON key file
                     {"private_key": "<64 hex>"} stored OUTSIDE this repo.
Optional:
  TZERO_RPC          Base Sepolia JSON-RPC URL (default https://sepolia.base.org)
  TZERO_STATE        path to state.json (default ./state.json)
  TZERO_WHITELIST    path to whitelist.json (default ./whitelist.json)
  TZERO_BUY_ETH      ETH per auto-buy (default 0.01)
"""
import json
import os

# --- public, on-chain constants (Base Sepolia, chain 84532) ---
CHAIN_ID = 84532
RPC = os.environ.get("TZERO_RPC", "https://sepolia.base.org")
API = "https://testnet.tzer0.fun/api/missions"
LAUNCHPAD = "0xbce48917C41BB45Ad15f859E4Bf0e4EEa74bAd5c"   # tzer0 launchpad
ROUTER = "0x21BbD201c69d0091707d0367D3AD6875cEc150e1"      # TzeroSwap (custom router)
WETH = "0x4200000000000000000000000000000000000006"
BUY_ETH = float(os.environ.get("TZERO_BUY_ETH", "0.01"))
BUY_SELECTOR = "cce7ec13"  # buy(address token, uint256 minTokensOut)
SELL_SELECTOR = "1d629f11"  # custom sell(address,uint256,uint256,address,uint256)

STATE_PATH = os.environ.get("TZERO_STATE", os.path.join(os.path.dirname(__file__), "state.json"))
WHITELIST_PATH = os.environ.get("TZERO_WHITELIST", os.path.join(os.path.dirname(__file__), "whitelist.json"))


def load_account():
    """Return an eth_account Account from the environment. Never prints the key."""
    from eth_account import Account

    key = os.environ.get("TZERO_PRIVATE_KEY")
    if not key:
        key_file = os.environ.get("TZERO_KEY_FILE")
        if not key_file:
            raise RuntimeError(
                "no wallet key: set TZERO_PRIVATE_KEY (64 hex, no 0x) "
                "or TZERO_KEY_FILE (path to a JSON key file outside this repo)"
            )
        with open(key_file) as f:
            key = json.load(f)["private_key"]
    key = key.strip()
    if key.startswith("0x"):
        key = key[2:]
    if len(key) != 64:
        raise RuntimeError("TZERO_PRIVATE_KEY must be 64 hex chars")
    return Account.from_key(key)
