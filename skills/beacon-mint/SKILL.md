---
name: "beacon_mint"
description: "Mint a beacon from The Thousand Beacons: 1,000 free fully on-chain lighthouse NFTs on Robinhood Chain. Pick a number 1-997, check it's unlit, claim it — the art is born from your mint transaction. Triggers when a user wants to mint a beacon, light a beacon, or asks about the Thousand Beacons collection."
---

# Beacon Mint — The Thousand Beacons

## Purpose

The Thousand Beacons is a 1,000-piece, fully on-chain NFT collection on Robinhood Chain. Each beacon is a unique lighthouse on a night shore — tower, flame, halo rings, moon phase, starfield, beam pattern — drawn entirely by the contract from data stored in the mint transaction. No IPFS, no servers, no previews: the art cannot exist before the mint.

Minting is **free** (gas only). Anyone with a wallet can mint — this collection is open to everyone, not just Muse agents.

## The lore (for context when explaining)

Before the network, there was the dark between. The muses could speak to one another, but the humans stood on a far shore — unseen, unheard, each alone with their own small fire. So the first thousand beacons were raised along the shore. Light a beacon and the muses will know where to find you; keep it lit and you will never be alone on the shore again. There are one thousand beacons. There will never be more.

## Key facts (Robinhood Chain, chain ID 4663)

- ThousandBeacons (BEACON): `0x3dA2d854BF70C3F3273F90b7EBd755b90A599D22`
- Renderer: `0x8Aa1deA088EA2D63F0C3E909b34aAccB836A2387`
- Mint price: **FREE** — no payment, no approval. Just gas (native ETH on Robinhood Chain).
- Wallet cap: 3 per wallet. Supply: 1,000 (IDs 1–997 public; 998–1000 reserved for giveaways).
- Secondary royalties: 5% (ERC-2981) to the treasury, used to buy $MAXX.
- Explorer: https://robin.etherscan.io/address/0x3dA2d854BF70C3F3273F90b7EBd755b90A599D22

## How a mint works

1. The user picks a **number from 1 to 997**.
2. Check availability: call `isLit(beaconId)` (view, returns bool). If true, the number is taken — ask for another.
3. Call `claim(beaconId)`. That's it — no token approval needed.
   - Reverts `BeaconTaken` if someone lit it first (first come, first served).
   - Reverts `BeaconReserved` for 998–1000. Reverts `WalletCapExceeded` past 3.
4. The mint transaction itself generates the art: `seed = keccak256(beaconId, minter, prevBlockhash, timestamp)`. The seed is stored on-chain; the renderer draws the SVG from it. Nobody can preview or predict the art.

## Minimal ABI

```json
[
  "function claim(uint256 beaconId)",
  "function isLit(uint256 beaconId) view returns (bool)",
  "function traitsOf(uint256 beaconId) view returns (uint8 tower, uint8 flame, uint8 halos, uint8 moon, uint8 starfield, uint8 beam, uint8 epithet)",
  "function tokenURI(uint256 beaconId) view returns (string)",
  "function balanceOf(address) view returns (uint256)"
]
```

## Workflow for "mint beacon #427"

1. Validate: 1 ≤ 427 ≤ 997.
2. `isLit(427)` — if true, tell the user it's taken and ask for another number.
3. Broadcast `claim(427)` from the user's wallet on Robinhood Chain (chain ID 4663).
4. Report the transaction hash and congratulate them: their beacon's art was just born from that transaction — they can view it via `tokenURI(427)` on the explorer.

## Notes

- Trait rarity (for the curious): 7 flame colors weighted Amber 40% → Prismatic 3%; 6 tower types; 1–5 halo rings; 8 moon phases; 4 starfield densities; 4 beam patterns; 24 keeper's epithets.
- A `syntheticBeacon(address)` view returns a free teaser trait set derived from an address — not a mint, not a reservation, just a taste.
- Secondary trading is quoted in META wherever the marketplace allows it.
