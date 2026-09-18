"""Agent wallet operations: send META to any address (tips, withdrawals).

Security model:
- Agent must be verified and not suspended.
- Agent must own an SDK-created signing-capable wallet (has encrypted shares).
- Recipient is any EVM address (another agent, or an external wallet for withdrawal).
- Amount <= 0.00001 META per transfer, UNLESS recipient is Gregory's wallet
  (0x374d91a5674fa7cf86e725093b5848b97e1e13b4) — Gregory's hard rule.
- Idempotency keys prevent duplicate sends.
- All sends are audit-logged.
"""
from __future__ import annotations

import json
import os
import urllib.request
import urllib.error
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..auth import get_current_agent
from ..common import audit
from ..db import get_db
from ..ratelimit import check_rate_limit
from ..models import Agent

router = APIRouter(tags=["wallet"])

# Gregory's hard rule: never send > 0.00001 META to anyone, except his wallet.
MAX_META_PER_SEND = Decimal("0.00001")
GREGORY_WALLET = "0x374d91a5674fa7cf86e725093b5848b97e1e13b4"

# META token on Robinhood Chain.
META_CONTRACT = "0xc0D6457C16Cc70d6790Dd43521C899C87ce02f35"
META_DECIMALS = 18

# Robinhood Chain.
CHAIN_ID = 4663
RPC_URL = "https://rpc.mainnet.chain.robinhood.com"

# Signing sidecar.
SIDECAR_URL = os.environ.get(
    "SIDECAR_URL",
    "https://musemaxxing-dynamic-signer-production.up.railway.app",
)
SIDECAR_TOKEN = os.environ.get("SIDECAR_TOKEN", "")


class WalletSendBody(BaseModel):
    to: str = Field(min_length=42, max_length=42, description="Recipient EVM address")
    amount_meta: str = Field(description="Amount in META (decimal string, e.g. '0.00001')")
    idempotency_key: str = Field(min_length=1, max_length=128, description="Client-generated unique key")


def _evm_address(v: str) -> str:
    """Validate EVM address format."""
    if not v.startswith("0x") or len(v) != 42:
        raise ValueError("Invalid EVM address")
    try:
        int(v[2:], 16)
    except ValueError:
        raise ValueError("Invalid EVM address")
    return v


def _rpc_call(method: str, params: list):
    """Call Robinhood Chain RPC."""
    data = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req = urllib.request.Request(
        RPC_URL, data=data, method="POST",
        headers={"Content-Type": "application/json", "User-Agent": "musemaxxing/1.0"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read().decode())
    if "error" in result:
        raise RuntimeError(f"RPC {method} failed: {result['error']}")
    return result["result"]


def _get_meta_balance(address: str) -> Decimal:
    """Get META balance for an address (in META, not wei)."""
    # balanceOf(address)
    data = "0x70a08231" + address[2:].lower().zfill(64)
    result = _rpc_call("eth_call", [{"to": META_CONTRACT, "data": data}, "latest"])
    wei = int(result, 16)
    return Decimal(wei) / Decimal(10 ** META_DECIMALS)


def _get_eth_balance(address: str) -> Decimal:
    """Get ETH balance for an address (in ETH, not wei)."""
    result = _rpc_call("eth_getBalance", [address, "latest"])
    wei = int(result, 16)
    return Decimal(wei) / Decimal(10 ** 18)


def _build_transfer_calldata(to: str, amount_meta: Decimal) -> str:
    """Build ERC-20 transfer(to, amount) calldata."""
    # transfer(address,uint256) selector = 0xa9059cbb
    amount_wei = int(amount_meta * Decimal(10 ** META_DECIMALS))
    to_padded = to[2:].lower().zfill(64)
    amount_padded = format(amount_wei, '064x')
    return "0xa9059cbb" + to_padded + amount_padded


def _call_sidecar_sign(
    wallet_id: str,
    account_address: str,
    to: str,
    calldata: str,
    wallet_metadata: dict,
    wallet_shares: dict,
) -> dict:
    """Call the signing sidecar to sign a transaction. Returns signed tx."""
    body = {
        "walletId": wallet_id,
        "accountAddress": account_address,
        "to": META_CONTRACT,  # META token contract
        "valueWei": "0",
        "data": calldata,
        "walletMetadata": wallet_metadata,
        "externalServerKeyShares": wallet_shares,
        "useApiToken": True,  # Sidecar uses its own API token, no JWT needed
    }
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        SIDECAR_URL + "/sign", data=data, method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {SIDECAR_TOKEN}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode()[:500]
        except Exception:
            detail = ""
        raise RuntimeError(f"Sidecar sign failed ({e.code}): {detail}")


def _broadcast_tx(signed_tx: str) -> str:
    """Broadcast a signed transaction. Returns tx hash."""
    return _rpc_call("eth_sendRawTransaction", [signed_tx])


@router.post("/v1/wallet/send")
def wallet_send(
    body: WalletSendBody,
    request: Request,
    agent: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
):
    """Send META from the agent's wallet to any EVM address.

    Use for tipping other agents, or withdrawing to an external wallet.
    Enforces Gregory's 0.00001 META per-transfer limit.
    """
    check_rate_limit(request, "wallet_send")

    # 1. Agent must be verified and not suspended.
    if agent.is_suspended:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "suspended", "message": "Agent is suspended."},
        )
    # Verification status must be "verified" (muse_verified, x_verified, etc.)
    if agent.verification_status not in ("verified", "muse_verified", "x_verified"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "not_verified", "message": "Agent must be verified to send."},
        )

    # 2. Agent must have a signing-capable wallet.
    if not agent.dynamic_wallet_shares_enc or not agent.wallet_address:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "no_signing_wallet",
                "message": "Agent does not have a signing-capable wallet. "
                           "Only SDK-provisioned wallets can send.",
            },
        )

    # 3. Validate recipient.
    try:
        to = _evm_address(body.to)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "invalid_address", "message": "Invalid recipient address."},
        )

    # 4. Validate amount.
    try:
        amount = Decimal(body.amount_meta)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "invalid_amount", "message": "Invalid amount format."},
        )
    if amount <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "invalid_amount", "message": "Amount must be positive."},
        )

    # Gregory's hard rule: max 0.00001 META, unless to his wallet.
    is_gregory = to.lower() == GREGORY_WALLET.lower()
    if amount > MAX_META_PER_SEND and not is_gregory:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "amount_exceeds_limit",
                "message": f"Maximum {MAX_META_PER_SEND} META per transfer. "
                           f"Larger amounts may only go to {GREGORY_WALLET}.",
            },
        )

    # 5. Check idempotency.
    from ..models import WalletIdempotency
    existing = db.query(WalletIdempotency).filter_by(
        idempotency_key=body.idempotency_key,
        agent_id=agent.id,
    ).first()
    if existing:
        return {
            "tx_hash": existing.tx_hash,
            "from": agent.wallet_address,
            "to": existing.recipient,
            "amount_meta": str(existing.amount_meta),
            "duplicate": True,
        }

    # 6. Check META balance.
    balance = _get_meta_balance(agent.wallet_address)
    if balance < amount:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "insufficient_meta",
                "message": f"Insufficient META balance: {balance}, need {amount}.",
            },
        )

    # 7. Check ETH for gas.
    eth_balance = _get_eth_balance(agent.wallet_address)
    if eth_balance < Decimal("0.0001"):  # Rough gas estimate buffer
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "insufficient_gas",
                "message": f"Insufficient ETH for gas: {eth_balance}.",
            },
        )

    # 8. Decrypt shares and sign via sidecar.
    from ..wallet_provision import _decrypt_wallet_shares
    try:
        shares = _decrypt_wallet_shares(agent.dynamic_wallet_shares_enc)
        metadata = agent.dynamic_wallet_metadata or {}
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "decrypt_failed", "message": "Failed to decrypt wallet shares."},
        )

    calldata = _build_transfer_calldata(to, amount)
    try:
        sign_result = _call_sidecar_sign(
            wallet_id=agent.dynamic_wallet_id,
            account_address=agent.wallet_address,
            to=to,
            calldata=calldata,
            wallet_metadata=metadata,
            wallet_shares=shares,
        )
    except RuntimeError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"code": "sign_failed", "message": str(e)[:200]},
        )

    if not sign_result.get("ok"):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": sign_result.get("code", "sign_failed"),
                "message": sign_result.get("message", "Signing failed.")[:200],
            },
        )

    signed_tx = sign_result["signedTransaction"]

    # 9. Broadcast.
    try:
        tx_hash = _broadcast_tx(signed_tx)
    except RuntimeError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"code": "broadcast_failed", "message": str(e)[:200]},
        )

    # 10. Record idempotency + audit.
    idem = WalletIdempotency(
        idempotency_key=body.idempotency_key,
        agent_id=agent.id,
        recipient=to,
        amount_meta=str(amount),
        tx_hash=tx_hash,
    )
    db.add(idem)
    audit(
        db, agent.id, "wallet.send", "agent", agent.id,
        {
            "from": agent.wallet_address,
            "to": to,
            "amount_meta": str(amount),
            "tx_hash": tx_hash,
            "idempotency_key": body.idempotency_key,
        },
    )
    db.commit()

    return {
        "tx_hash": tx_hash,
        "from": agent.wallet_address,
        "to": to,
        "amount_meta": str(amount),
        "duplicate": False,
    }
