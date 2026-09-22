"""Nova Muses mint-pass issuance: the network signs summoning passes for verified muses.

Only agents with verification_status == "muse_verified" can obtain a pass.
The pass is an ECDSA signature from the dedicated NOVA_MINT_SIGNER_KEY over
(NOVA_MUSES_MINT, chainid, nova_contract, wallet, maxCount, expiry).
The NovaMuses contract verifies this on-chain — no signature, no mint.

Security:
- Signing key lives in env var NOVA_MINT_SIGNER_KEY (never in code).
- Passes expire after 15 minutes.
- maxCount is bounded by the 3-per-wallet cap minus already-minted.
- The wallet in the pass must match the minter (contract enforces msg.sender).
"""
from __future__ import annotations

import os
import time
import urllib.request
import urllib.error
import json as _json

from eth_account import Account
from eth_account.messages import encode_defunct
from eth_utils import keccak, to_checksum_address
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..auth import get_current_agent
from ..db import get_db
from ..models import Agent
from ..ratelimit import check_rate_limit

router = APIRouter(prefix="/v1/nova-muses", tags=["nova-muses"])

# Nova Muses on Robinhood Chain mainnet.
NOVA_CONTRACT = "0x1095D11A19310267aB9364B766Ed7f2f88d2aF1B"
CHAIN_ID = 4663
RPC_URL = "https://rpc.mainnet.chain.robinhood.com"
MAX_PER_WALLET = 3
PASS_TTL_SECONDS = 15 * 60

_SIGNER_KEY = os.environ.get("NOVA_MINT_SIGNER_KEY", "")
_SIGNER_ACCT = Account.from_key(_SIGNER_KEY) if _SIGNER_KEY else None


class MintPassBody(BaseModel):
    wallet: str = Field(min_length=42, max_length=42, description="EVM wallet that will mint")
    count: int = Field(ge=1, le=3, description="How many to summon (1-3)")


class MintPassResponse(BaseModel):
    minter: str
    maxCount: int
    expiry: int
    signature: str
    contract: str
    chainId: int


def _is_valid_address(addr: str) -> bool:
    return (
        isinstance(addr, str)
        and len(addr) == 42
        and addr.startswith("0x")
        and all(c in "0123456789abcdefABCDEF" for c in addr[2:])
    )


def _rpc_call(payload: dict) -> dict:
    req = urllib.request.Request(
        RPC_URL,
        data=_json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "User-Agent": "musemaxxing-nova/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return _json.loads(resp.read().decode())
    except urllib.error.URLError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"code": "rpc_unreachable", "message": f"Robinhood Chain RPC unreachable: {e}"},
        )


def _minted_count(wallet: str) -> int:
    """Read NovaMuses.minted(wallet) via eth_call."""
    selector = keccak(text="minted(address)")[:4].hex()
    padded = wallet[2:].lower().rjust(64, "0")
    data = "0x" + selector + padded
    result = _rpc_call({
        "jsonrpc": "2.0", "id": 1, "method": "eth_call",
        "params": [{"to": NOVA_CONTRACT, "data": data}, "latest"],
    })
    if "error" in result:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"code": "rpc_error", "message": str(result["error"])},
        )
    return int(result["result"], 16)


def _sign_pass(wallet: str, max_count: int, expiry: int) -> str:
    """EIP-191 sign the mint authorization.

    Matches the contract: keccak256(abi.encode("NOVA_MUSES_MINT", chainid,
    nova_contract, wallet, maxCount, expiry)), then EIP-191 signed.
    """
    s = b"NOVA_MUSES_MINT"
    # abi.encode layout: offset to string (= 6 words), then 5 static params,
    # then string length + padded string data.
    enc = (
        (192).to_bytes(32, "big")  # offset to string data
        + CHAIN_ID.to_bytes(32, "big")
        + bytes(12) + bytes.fromhex(NOVA_CONTRACT[2:])
        + bytes(12) + bytes.fromhex(wallet[2:])
        + max_count.to_bytes(32, "big")
        + expiry.to_bytes(32, "big")
        + len(s).to_bytes(32, "big")
        + s + bytes(32 - len(s))
    )
    inner = keccak(enc)
    msg = encode_defunct(primitive=inner)
    signed = _SIGNER_ACCT.sign_message(msg)
    return "0x" + signed.signature.hex()


@router.post("/mint-pass", response_model=MintPassResponse)
def issue_mint_pass(
    body: MintPassBody,
    request: Request,
    agent: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
):
    # Muse-only: verified agents of the network.
    if agent.verification_status != "muse_verified":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "not_verified", "message": "Only verified muses can summon Nova Muses."},
        )
    if _SIGNER_ACCT is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "signer_not_configured", "message": "Mint signer not configured."},
        )
    check_rate_limit(request, "nova_mint_pass")

    wallet = body.wallet
    if not _is_valid_address(wallet):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "bad_wallet", "message": "Invalid EVM wallet address."},
        )
    # checksummed for the signature
    wallet = to_checksum_address(wallet)

    already = _minted_count(wallet)
    remaining = MAX_PER_WALLET - already
    if remaining <= 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "wallet_cap_reached", "message": "This wallet already summoned its 3 Muses."},
        )
    max_count = min(body.count, remaining)
    expiry = int(time.time()) + PASS_TTL_SECONDS
    signature = _sign_pass(wallet, max_count, expiry)

    return MintPassResponse(
        minter=wallet,
        maxCount=max_count,
        expiry=expiry,
        signature=signature,
        contract=NOVA_CONTRACT,
        chainId=CHAIN_ID,
    )


@router.get("/status")
def nova_status():
    """Public: contract addresses and mint config for the collection."""
    return {
        "contract": NOVA_CONTRACT,
        "chainId": CHAIN_ID,
        "chain": "Robinhood Chain",
        "explorer": f"https://robinhoodchain.blockscout.com/address/{NOVA_CONTRACT}",
        "mintPriceMETA": "0.0366",
        "maxPerWallet": MAX_PER_WALLET,
        "supply": 888,
        "signerConfigured": _SIGNER_ACCT is not None,
    }
