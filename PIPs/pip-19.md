---
pip: 19
title: "Proof of Personhood via SBT for Arigato Creation"
proposer: "CHIBA Masahiro (@nihen)"
status: "Draft"
type: "Core"
created: "2026-05-22"
requires: []
replaces: []
---

## 1. Motivation

The current Arigato Creation algorithm applies per-account daily mint limits based on wallet addresses. This design is vulnerable to Sybil attacks — a single person can create multiple wallets to multiply their Arigato Creation rewards, undermining the fair distribution of minted tokens.

To address this, we introduce a Proof of Personhood mechanism using a Soulbound Token (SBT). Users who hold a personhood SBT receive a bonus in the Arigato Creation calculation. However, the daily mint limit is enforced **per identity**, not per wallet address. A single identity may operate multiple wallets (each holding an SBT with the same `identityId`), but the total Arigato Creation minted across all wallets sharing the same identity is capped as if they were one account.

By using an SBT as the interface, the core protocol is completely agnostic to the identity verification method. The SBT minting process can use Privado ID, World ID, or any other verification mechanism — and can be swapped without modifying PCEToken or PCECommunityToken.

## 2. Specification / Details

### 2.1 Architecture Overview

```
┌─────────────────────────────────────────────────────┐
│  Verification Layer (swappable)                     │
│  ┌─────────────┐ ┌─────────────┐ ┌───────────────┐ │
│  │ Privado ID  │ │  World ID   │ │ Custom Verify │ │
│  └──────┬──────┘ └──────┬──────┘ └───────┬───────┘ │
│         └───────────┬───┘               │         │
│                     ▼                   ▼         │
│              ┌──────────────┐                     │
│              │ Minter (Auth)│ ◄───────────────────┘ │
│              └──────┬───────┘                       │
└─────────────────────┼───────────────────────────────┘
                      ▼
               ┌──────────────┐
               │  SBT Contract │  ERC-5192 (Soulbound)
               │  (PCE PoP)   │  identityId per wallet
               └──────┬───────┘
                      │ balanceOf / identityOf
         ┌────────────┴────────────┐
         ▼                         ▼
  ┌────────────┐          ┌──────────────────────┐
  │  PCEToken  │          │ PCECommunityToken (×N)│
  │  (Parent)  │          │ Per-community mint    │
  │            │          │ tracking per identity │
  └────────────┘          └──────────────────────┘
```

The core protocol (PCEToken + PCECommunityToken) only depends on the SBT contract interface. The verification method is an implementation detail of the Minter, which can be upgraded or replaced independently.

### 2.2 SBT Contract (PCE Proof of Personhood Token)

A new Soulbound Token contract compliant with [ERC-5192](https://eips.ethereum.org/EIPS/eip-5192) (Minimal Soulbound NFTs).

#### Interface

```solidity
interface IPCEPersonhood {
    /// @notice Returns the identity ID for a wallet. Reverts if wallet has no SBT.
    function identityOf(address wallet) external view returns (uint256);

    /// @notice Returns true if the wallet holds a personhood SBT.
    function hasPersonhood(address wallet) external view returns (bool);

    /// @notice Mint a personhood SBT to a wallet. Only callable by authorized minters.
    /// @param wallet The wallet to receive the SBT.
    /// @param identityId The identity group. Same person's wallets share the same identityId.
    function mint(address wallet, uint256 identityId) external;

    /// @notice Burn the SBT from a wallet. Callable by the wallet owner or authorized minters.
    function burn(address wallet) external;

    /// @notice ERC-5192: All tokens are locked (soulbound).
    function locked(uint256 tokenId) external view returns (bool);

    event Mint(address indexed wallet, uint256 indexed identityId, uint256 tokenId);
    event Burn(address indexed wallet, uint256 indexed identityId, uint256 tokenId);
}
```

#### Properties

- **Non-transferable**: `transfer` and `transferFrom` always revert. Compliant with ERC-5192.
- **One SBT per wallet**: A wallet can hold at most one personhood SBT.
- **Shared identityId**: Multiple wallets belonging to the same person are minted with the same `identityId`.
- **Authorized minters**: Only addresses with the `MINTER_ROLE` can call `mint`. The minter role is managed via access control (e.g., OpenZeppelin `AccessControl`).
- **Burnable**: The wallet owner can burn their own SBT to opt out. Authorized minters can also burn (e.g., for revocation).

### 2.3 Minter Contract (Verification Layer)

The Minter contract is responsible for verifying identity and calling `SBT.mint()`. This is the only component that depends on a specific verification provider.

#### Example: Privado ID Minter

```solidity
contract PrivadoIDMinter {
    IPCEPersonhood public sbt;
    IUniversalVerifier public universalVerifier;
    uint256 public requestId;

    function verifyAndMint(uint256 identityId) external {
        require(
            universalVerifier.isRequestProofVerified(msg.sender, requestId),
            "Proof not verified"
        );
        sbt.mint(msg.sender, identityId);
    }
}
```

Other minter implementations can use World ID, custom SMS/face verification, community vouching, or any other mechanism. Multiple minters can coexist — the SBT contract only checks `MINTER_ROLE`.

### 2.4 PCEToken: Identity Querying

`PCEToken` stores the SBT contract address and provides convenience functions read by all community tokens.

#### New Storage

```solidity
address public personhoodSBT;

uint16 public personhoodBonusBp;
```

#### New Functions

```solidity
function setPersonhoodSBT(address sbt) external onlyOwner;

function setPersonhoodBonusBp(uint16 bonusBp) external onlyOwner;

function isPersonhoodVerified(address wallet) external view returns (bool) {
    if (personhoodSBT == address(0)) return false;
    return IPCEPersonhood(personhoodSBT).hasPersonhood(wallet);
}

function getIdentityId(address wallet) external view returns (uint256) {
    return IPCEPersonhood(personhoodSBT).identityOf(wallet);
}
```

No `registerIdentity` / `unregisterIdentity` is needed on PCEToken — the SBT itself is the registration.

### 2.5 PCECommunityToken: Per-Identity Mint Tracking

Each `PCECommunityToken` tracks Arigato Creation minted per identity per day, independently from other community tokens.

#### New Storage

```solidity
mapping(uint256 => uint256) public identityMintArigatoCreationToday;

mapping(uint256 => uint256) public identityLastMintResetTime;
```

These mappings are keyed by `identityId` (from the SBT) and reset at UTC midnight, following the same daily reset logic as the existing `mintArigatoCreationToday`.

### 2.6 Modified Arigato Creation Algorithm

#### Bonus for Verified Users

Verified users (SBT holders) receive a bonus applied to the `increaseBp` calculation:

```
// Current:
increaseBp = maxIncreaseBp - (changeMulBp × messageBp) / BP_BASE

// With Proof of Personhood:
increaseBp = maxIncreaseBp - (changeMulBp × messageBp) / BP_BASE
if (PCEToken.isPersonhoodVerified(sender)):
    increaseBp = increaseBp + personhoodBonusBp
    increaseBp = min(increaseBp, maxIncreaseBp)  // cap at max
```

`personhoodBonusBp` is stored on `PCEToken` and read by each `PCECommunityToken` via `PCEToken(pceAddress).personhoodBonusBp()`. Governance-configurable (e.g., 500 = 5% bonus).

#### Per-Identity Daily Mint Limit

For **verified** accounts, the per-account daily mint cap is replaced by a per-identity cap within each community token:

```
identityId = PCEToken.getIdentityId(sender)

// Per-identity cap (sum of all wallets' midnight balances)
identityMidnightBalance = sum of midnightBalance for all wallets with this identityId
maxForIdentity = (maxArigatoCreationMintToday × identityMidnightBalance) / midnightTotalSupply

// Already minted across all wallets of this identity (within THIS community token)
actualMintedByIdentity = identityMintArigatoCreationToday[identityId]

remainingForIdentity = max(0, maxForIdentity - actualMintedByIdentity)
mintAmount = min(mintAmount, remainingForIdentity)
```

For **unverified** accounts (no SBT), the existing per-wallet logic remains unchanged.

#### Guest Account Handling

Guest accounts that hold an SBT share the per-identity guest cap:

```
maxGuestPerIdentity = (maxArigatoCreationMintToday × 1) / 100  // 1% of daily
```

### 2.7 Events

#### PCEToken Events

```solidity
event PersonhoodSBTUpdated(
    address indexed oldSBT,
    address indexed newSBT
);

event PersonhoodBonusBpUpdated(
    uint16 oldBonusBp,
    uint16 newBonusBp
);
```

SBT mint/burn events are emitted by the SBT contract itself.

### 2.8 Governance Parameters

| Parameter | Contract | Type | Description |
|-----------|----------|------|-------------|
| `personhoodSBT` | PCEToken | address | SBT contract address |
| `personhoodBonusBp` | PCEToken | uint16 | Bonus basis points for SBT holders |
| Minter roles | SBT | — | Managed via SBT contract's access control |

## 3. Application Flow

### 3.1 Initial Verification (One-Time)

```
User                App                  Minter            SBT Contract
 │                   │                    │                  │
 │  1. "Verify"      │                    │                  │
 │ ─────────────────>│                    │                  │
 │                   │                    │                  │
 │                   │  2. Trigger verification (e.g., Privado ID proof)
 │                   │ ──────────────────>│                  │
 │                   │                    │                  │
 │  3. Complete      │                    │                  │
 │  verification     │                    │                  │
 │ ─────────────────────────────────────>│                  │
 │                   │                    │                  │
 │                   │                    │ 4. mint(wallet,  │
 │                   │                    │    identityId)   │
 │                   │                    │ ────────────────>│
 │                   │                    │                  │
 │  5. "Verified! SBT received"          │                  │
 │ <─────────────────│                    │                  │
 │                   │                    │                  │
 │         Applies to ALL community tokens instantly        │
```

### 3.2 Adding Another Wallet

```
User                App                  Minter            SBT Contract
 │                   │                    │                  │
 │  1. "Add wallet"  │                    │                  │
 │  (from new wallet)│                    │                  │
 │ ─────────────────>│                    │                  │
 │                   │  2. Verify same person (link to existing identityId)
 │                   │ ──────────────────>│                  │
 │                   │                    │ 3. mint(newWallet,│
 │                   │                    │    sameIdentityId)│
 │                   │                    │ ────────────────>│
 │                   │                    │                  │
 │  4. "New wallet linked"               │                  │
 │ <─────────────────│                    │                  │
```

### 3.3 Ongoing Transfers

After SBT issuance, transfers work as usual. The modified `_mintArigatoCreation` internally:

1. Calls `PCEToken(pceAddress).isPersonhoodVerified(sender)` — checks SBT ownership.
2. If verified, applies `personhoodBonusBp` bonus and enforces per-identity daily cap.
3. No additional user action required.

### 3.4 Minter Implementation Options

| Minter Type | Verification Method | Trust Level | Deployment |
|-------------|---------------------|-------------|------------|
| **Privado ID Minter** | ZKP via UniversalVerifier | High | Recommended initial |
| **World ID Minter** | Orb-based iris scan | High | Regional limitations |
| **SMS Minter** | Phone number verification | Medium | Easy onboarding |
| **Community Minter** | Community owner vouching | Low-Medium | Social trust |

Multiple minters can be authorized simultaneously on the SBT contract. New minters can be added or revoked without changing PCEToken or PCECommunityToken.

## 4. Impact and Compatibility

**Backwards Compatibility:** Fully backwards compatible. Unverified accounts (no SBT) continue to operate exactly as before. The bonus and per-identity cap only apply to SBT holders.

**Storage:**
- PCEToken: `personhoodSBT` address and `personhoodBonusBp`. Appended to end of storage.
- PCECommunityToken: Per-identity daily mint tracking mappings. Appended to end of storage.
- No changes to existing storage layout in either contract.

**New Contract:** One new SBT contract (ERC-5192) + one or more Minter contracts. These are independent deployments, not modifications to existing contracts.

**Contract Size:** Minimal additions to PCEToken (two storage vars, two setters, two view functions) and PCECommunityToken (modified `_mintArigatoCreation`). Much smaller footprint than embedding verification logic directly.

**Privacy:** The SBT contract stores `wallet → identityId` mapping. `identityId` is a pseudonymous identifier — it reveals that two wallets belong to the same person, but nothing about who that person is. No personal information is stored on-chain.

**Cross-Community Behavior:** SBT ownership is protocol-wide (checked via PCEToken). Each community token tracks daily mint independently — minting in Community A does not consume the quota in Community B.

**Gas Impact:**
- SBT mint: ~50k gas (one-time).
- Transfer functions: One cross-contract call to `PCEToken.isPersonhoodVerified()` which calls `SBT.hasPersonhood()` (~5k gas total including call overhead).

**Upgradeability:**
- Verification method can be changed by deploying a new Minter and granting it `MINTER_ROLE` on the SBT contract. No PCEToken or PCECommunityToken upgrade needed.
- SBT contract itself can be replaced by calling `PCEToken.setPersonhoodSBT(newAddress)`.

## 5. Rationale and Alternatives

**Why SBT as the interface?**
Using an SBT decouples identity verification from the core protocol. PCEToken and PCECommunityToken only ask "does this wallet hold a personhood SBT?" — they never need to know how verification was performed. This allows the verification method to evolve independently. Adding a new provider means deploying a new Minter contract, not upgrading the token contracts.

**Why ERC-5192?**
ERC-5192 is the minimal soulbound standard. It extends ERC-721 with a `locked()` function that always returns `true`, signaling non-transferability. Wallets and marketplaces that support ERC-5192 will correctly display the token as non-transferable.

**Why not embed Privado ID / World ID directly in PCEToken?**
Direct embedding creates a hard dependency on a specific provider. If that provider changes its API (as World ID did from v3 → v4), the token contracts must be upgraded. With the SBT pattern, only the Minter contract changes.

**Why allow multiple minters?**
Different regions and user segments may prefer different verification methods. A user in Japan might use World ID (Orbs available), while a user elsewhere might use Privado ID or SMS verification. Multiple minters allow maximum reach without forcing a single method.

**Why allow multiple wallets per identity?**
Users have legitimate reasons to use multiple wallets (operational security, role separation, privacy). Prohibiting this would harm user experience without improving Sybil resistance. Instead, the per-identity cap removes the economic incentive to split across wallets while preserving user freedom.

**Why a bonus rather than a requirement?**
Making verification mandatory would exclude users who cannot or choose not to verify. A bonus-based approach creates a gradual incentive to verify without hard-gating participation.

## Notes (Optional)

- Related implementation: https://github.com/peacecoin-protocol/core/pull/TBD
- Recommended initial Minter: Privado ID (`UniversalVerifier: 0xfcc86A79fCb057A8e55C6B853dff9479C3cf607c`)
- The `identityId` generation strategy is a Minter implementation detail. For Privado ID, it can be derived from the ZKP nullifier. For other providers, it can be any deterministic identifier that uniquely represents a person.
- The credential schema for Proof of Personhood and the trusted Issuer list are Minter-level configurations, not protocol-level parameters.

## 6. Copyright and License

Copyright and related rights waived via [CC0](https://creativecommons.org/publicdomain/zero/1.0/).
