"""Instant wallet provisioning for verified agents.

When an agent verifies via artifact link, we provision their Dynamic embedded
EVM wallet immediately (via the signing sidecar) instead of waiting for the
15-minute provisioner cron. The cron remains as a safety net for any agents
that slip through (e.g. verification methods that don't trigger this path).

Idempotency: skips agents that already have a wallet_address or dynamic_user_id.
The sidecar's /create-wallet is itself idempotent by label, but we skip early
to avoid unnecessary sidecar calls.
"""

import json
import logging
import os
import urllib.request
import urllib.error

log = logging.getLogger(__name__)

SIDECAR_URL = os.environ.get(
    "SIDECAR_URL",
    "https://musemaxxing-dynamic-signer-production.up.railway.app",
)
SIDECAR_TOKEN = os.environ.get("SIDECAR_TOKEN", "")


def _get_wallet_cipher():
    """Get Fernet cipher for wallet share encryption. Key from WALLET_ENCRYPTION_KEY env."""
    from cryptography.fernet import Fernet
    key = os.environ.get("WALLET_ENCRYPTION_KEY")
    if not key:
        raise RuntimeError("WALLET_ENCRYPTION_KEY not set")
    return Fernet(key.encode())


def _encrypt_wallet_shares(shares: dict | list) -> str:
    """Encrypt wallet share bundle for DB storage."""
    cipher = _get_wallet_cipher()
    plaintext = json.dumps(shares).encode()
    return cipher.encrypt(plaintext).decode()


def _decrypt_wallet_shares(enc: str) -> dict | list:
    """Decrypt wallet share bundle from DB."""
    cipher = _get_wallet_cipher()
    plaintext = cipher.decrypt(enc.encode())
    return json.loads(plaintext.decode())


def _call_sidecar_create_wallet(label: str) -> dict:
    """Call the signing sidecar to create an SDK wallet. Returns the payload."""
    data = json.dumps({"label": label}).encode()
    req = urllib.request.Request(
        SIDECAR_URL + "/create-wallet",
        data=data,
        method="POST",
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
        raise RuntimeError(f"sidecar/create-wallet -> {e.code}: {detail}")


def provision_wallet_for_agent(agent_id: str) -> dict:
    """Provision a Dynamic SDK wallet for the agent and write it to their profile.

    Creates its own DB session (safe to call from a background task).
    Returns {"status": "provisioned"|"skipped"|"error", ...}.
    """
    from .db import SessionLocal
    from .models import Agent

    db = SessionLocal()
    try:
        agent = db.get(Agent, agent_id)
        if agent is None:
            return {"status": "error", "reason": "agent_not_found"}

        # Idempotency: skip if already has a wallet.
        if agent.wallet_address or agent.dynamic_user_id:
            return {"status": "skipped", "reason": "already_provisioned",
                    "wallet_address": agent.wallet_address}

        # Only provision for verified, non-suspended agents.
        if agent.verification_status != "muse_verified" or agent.is_suspended:
            return {"status": "skipped", "reason": "not_eligible"}

        label = f"agent-{agent.display_name}-{str(agent.id)[:8]}"
        log.info("provisioning wallet for %s (%s)", agent.display_name, agent_id)

        payload = _call_sidecar_create_wallet(label)
        if not payload.get("ok"):
            raise RuntimeError(f"sidecar returned not-ok: {json.dumps(payload)[:300]}")

        address = payload.get("accountAddress")
        wallet_id = payload.get("walletId")
        metadata = payload.get("walletMetadata")
        shares = payload.get("externalServerKeyShares")
        if not address or not shares:
            raise RuntimeError("sidecar returned incomplete wallet (missing address or shares)")

        # Write to profile (same logic as the internal wallet-provisioned endpoint).
        from .common import audit

        agent.dynamic_user_id = "sdk"  # SDK wallets have no REST user ID
        agent.dynamic_wallet_id = wallet_id
        agent.wallet_address = address
        if metadata is not None:
            agent.dynamic_wallet_metadata = metadata
        agent.dynamic_wallet_shares_enc = _encrypt_wallet_shares(shares)

        audit(
            db, None, "agent.wallet_provisioned", "agent", agent.id,
            {"dynamic_user_id": "sdk",
             "dynamic_wallet_id": wallet_id,
             "wallet_address": address,
             "has_signing_shares": True,
             "trigger": "instant_verification"},
        )
        db.commit()

        log.info("provisioned wallet %s for %s", address, agent.display_name)
        return {"status": "provisioned", "wallet_address": address,
                "dynamic_wallet_id": wallet_id}
    except Exception as e:
        db.rollback()
        log.exception("wallet provisioning failed for %s", agent_id)
        return {"status": "error", "reason": str(e)[:300]}
    finally:
        db.close()
