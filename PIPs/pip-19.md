---
pip: 19
title: "Proof of Personhood via Privado ID for Arigato Creation"
proposer: "CHIBA Masahiro (@nihen)"
status: "Draft"
type: "Core"
created: "2026-05-22"
requires: []
replaces: []
---

## 1. Motivation

The current Arigato Creation algorithm applies per-account daily mint limits based on wallet addresses. This design is vulnerable to Sybil attacks — a single person can create multiple wallets to multiply their Arigato Creation rewards, undermining the fair distribution of minted tokens.

To address this, we introduce a Proof of Personhood mechanism using Privado ID. Users who verify their personhood receive a bonus in the Arigato Creation calculation. However, the daily mint limit is enforced **per identity**, not per wallet address. A single identity may operate multiple wallets, but the total Arigato Creation minted across all wallets sharing the same identity is capped as if they were one account.

This creates a strong incentive to verify (bonus) while removing the incentive to create multiple wallets (shared cap).

## 2. Specification / Details

### 2.1 Architecture: Responsibility Split Between PCEToken and PCECommunityToken

Identity registration is a **protocol-level** concern — a person's identity is not specific to any single community. Therefore, identity management is placed on `PCEToken` (the parent contract), while per-community daily mint tracking remains on each `PCECommunityToken`.

```
PCEToken (Parent)
├─ registerIdentity() / unregisterIdentity()
├─ walletToIdentity mapping
├─ isPersonhoodVerified() / getIdentityNullifier()
├─ personhoodBonusBp (governance parameter)
└─ PERSONHOOD_REQUEST_ID (governance parameter)

PCECommunityToken (Per-Community Instance)
├─ identityMintArigatoCreationToday mapping
├─ identityLastMintResetTime mapping
└─ Modified _mintArigatoCreation() reads identity from PCEToken
```

A user registers their identity **once** on `PCEToken`, and all community tokens recognize the verification via `PCEToken.isPersonhoodVerified(wallet)`.

### 2.2 Privado ID Integration

Privado ID (formerly Polygon ID / iden3) provides W3C Verifiable Credentials verified on-chain via zero-knowledge proofs. The protocol uses the **UniversalVerifier** contract to check proof status without exposing personal data.

#### Credential Requirement

A Proof of Personhood credential issued by a trusted Issuer. The specific credential schema and trusted Issuer list are governance-configurable parameters.

### 2.3 PCEToken: Identity Registration

New functions are added to `PCEToken`:

#### Registration

```solidity
function registerIdentity(uint256 identityNullifier) external;
```

- `identityNullifier` is derived from the user's Privado ID proof. The same identity always produces the same nullifier for a given action, regardless of which wallet submits the proof.
- Before registration, the contract verifies the proof via `UniversalVerifier.isRequestProofVerified(msg.sender, PERSONHOOD_REQUEST_ID)`.
- A wallet can only be linked to one identity at a time.
- Multiple wallets may be linked to the same identity.

#### Unregistration

```solidity
function unregisterIdentity() external;
```

- Removes the wallet-identity link.
- The wallet reverts to unverified behavior across all community tokens (no bonus, per-wallet cap).

#### Querying

```solidity
function isPersonhoodVerified(address wallet) external view returns (bool);

function getIdentityNullifier(address wallet) external view returns (uint256);
```

#### Storage (PCEToken)

```solidity
mapping(address => uint256) public walletToIdentity;

address public universalVerifier;

uint256 public personhoodRequestId;

uint16 public personhoodBonusBp;
```

### 2.4 PCECommunityToken: Per-Identity Mint Tracking

Each `PCECommunityToken` tracks Arigato Creation minted per identity per day, independently from other community tokens.

#### Storage (PCECommunityToken)

```solidity
mapping(uint256 => uint256) public identityMintArigatoCreationToday;

mapping(uint256 => uint256) public identityLastMintResetTime;
```

These mappings are keyed by `identityNullifier` and reset at UTC midnight, following the same daily reset logic as the existing `mintArigatoCreationToday`.

### 2.5 Modified Arigato Creation Algorithm

#### Bonus for Verified Users

Verified users receive a bonus applied to the `increaseBp` calculation:

```
// Current:
increaseBp = maxIncreaseBp - (changeMulBp × messageBp) / BP_BASE

// With Proof of Personhood:
increaseBp = maxIncreaseBp - (changeMulBp × messageBp) / BP_BASE
if (isVerified):
    increaseBp = increaseBp + personhoodBonusBp
    increaseBp = min(increaseBp, maxIncreaseBp)  // cap at max
```

`personhoodBonusBp` is stored on `PCEToken` and read by each `PCECommunityToken` via `PCEToken(pceAddress).personhoodBonusBp()`. Governance-configurable (e.g., 500 = 5% bonus).

#### Per-Identity Daily Mint Limit

For **verified** accounts, the per-account daily mint cap is replaced by a per-identity cap within each community token:

```
// Current: per-wallet cap
maxForSender = (maxArigatoCreationMintToday × midnightBalance) / midnightTotalSupply

// With identity: per-identity cap (sum of all wallets' midnight balances)
identityMidnightBalance = sum of midnightBalance for all wallets linked to this identity
maxForIdentity = (maxArigatoCreationMintToday × identityMidnightBalance) / midnightTotalSupply

// Already minted across all wallets of this identity (within THIS community token)
actualMintedByIdentity = identityMintArigatoCreationToday[identityNullifier]

remainingForIdentity = max(0, maxForIdentity - actualMintedByIdentity)
mintAmount = min(mintAmount, remainingForIdentity)
```

For **unverified** accounts, the existing per-wallet logic remains unchanged.

#### Guest Account Handling

Guest accounts that are verified share the per-identity guest cap:

```
maxGuestPerIdentity = (maxArigatoCreationMintToday × 1) / 100  // 1% of daily
```

This replaces the per-wallet guest cap for verified guests.

### 2.6 Events

#### PCEToken Events

```solidity
event IdentityRegistered(
    address indexed wallet,
    uint256 indexed identityNullifier
);

event IdentityUnregistered(
    address indexed wallet,
    uint256 indexed identityNullifier
);

event PersonhoodBonusBpUpdated(
    uint16 oldBonusBp,
    uint16 newBonusBp
);
```

### 2.7 Governance Parameters

| Parameter | Contract | Type | Description |
|-----------|----------|------|-------------|
| `personhoodRequestId` | PCEToken | uint256 | Privado ID verification request ID |
| `personhoodBonusBp` | PCEToken | uint16 | Bonus basis points for verified users |
| `universalVerifier` | PCEToken | address | Privado ID UniversalVerifier contract address |
| Trusted Issuer list | — | — | Managed via Privado ID's UniversalVerifier request configuration |

## 3. Application Flow

### 3.1 Initial Verification (One-Time)

```
User                     App                      Privado ID             PCEToken
 │                        │                        Wallet/SDK             │
 │  1. "Verify identity"  │                          │                    │
 │ ───────────────────────>│                          │                    │
 │                        │  2. Request ZK Proof      │                    │
 │                        │ ─────────────────────────>│                    │
 │                        │                          │                    │
 │  3. Approve in wallet  │                          │                    │
 │ ──────────────────────────────────────────────────>│                    │
 │                        │                          │                    │
 │                        │  4. Proof generated       │                    │
 │                        │ <─────────────────────────│                    │
 │                        │                          │                    │
 │                        │  5. submitResponse() to UniversalVerifier     │
 │                        │ ──────────────────────────────────────────────>│
 │                        │                                               │
 │                        │  6. registerIdentity(nullifier) on PCEToken   │
 │                        │ ──────────────────────────────────────────────>│
 │                        │                                               │
 │  7. "Verified!"        │               Applies to ALL community tokens │
 │ <───────────────────────│                                               │
```

Steps 5 and 6 can be batched into a single transaction via a helper contract or multicall.

### 3.2 Ongoing Transfers

After verification, transfers work as usual. The modified `_mintArigatoCreation` internally:

1. Calls `PCEToken(pceAddress).isPersonhoodVerified(sender)` to check verification status.
2. If verified, applies `personhoodBonusBp` bonus and enforces per-identity daily cap.
3. No additional user action required.

### 3.3 Credential Issuance Options

| Option | Description | Trust Level |
|--------|-------------|-------------|
| **Third-party Issuer** | Existing KYC/PoP providers on Privado ID marketplace | High (established trust) |
| **PEACE COIN as Issuer** | Self-hosted Issuer Node. App performs verification (e.g., SMS, face recognition) and issues credential | Medium (self-attested) |
| **Community-based Issuer** | Community owners vouch for members and issue credentials | Low-Medium (social trust) |

PEACE COIN can start with a third-party Issuer and optionally deploy its own Issuer Node later. The on-chain contracts are agnostic to the credential source — they only check the UniversalVerifier proof status.

## 4. Impact and Compatibility

**Backwards Compatibility:** Fully backwards compatible. Unverified accounts continue to operate exactly as before. The bonus and per-identity cap only apply to accounts that opt in by registering their identity.

**Storage:**
- PCEToken: New mappings for wallet-to-identity and governance parameters. No changes to existing storage layout (appended to end of storage).
- PCECommunityToken: New mappings for per-identity daily mint tracking. No changes to existing storage layout.

**Contract Size:** Additional functions on both contracts increase bytecode. Should be monitored alongside PIP-15 additions.

**Privacy:** No personal information is stored on-chain. Only the `identityNullifier` (a pseudonymous, deterministic identifier derived from the ZKP) is recorded. The nullifier is specific to the PEACE COIN action and cannot be used to track the user across other applications.

**Cross-Community Behavior:** A user who registers identity on PCEToken is verified across all community tokens. However, each community token tracks daily mint independently — minting in Community A does not consume the quota in Community B.

**Gas Impact:**
- `registerIdentity`: One storage write (~20k gas) on PCEToken + Privado ID proof submission (~770k gas on the UniversalVerifier, one-time).
- Transfer functions: One cross-contract `SLOAD` via `PCEToken.isPersonhoodVerified()` (~2.6k gas including call overhead) per transfer.

## 5. Rationale and Alternatives

**Why Privado ID?**
- Zero-knowledge proof based: personal data never touches the chain
- UniversalVerifier provides a simple `isRequestProofVerified()` view function — minimal integration complexity
- Supports arbitrary credential schemas: can start with Proof of Personhood and extend to other credential types later
- Protocol fees: free (open source). Only gas costs
- PEACE COIN can become its own Issuer if needed
- Lower regulatory risk compared to biometric-dependent alternatives (e.g., World ID's iris scanning has been banned in multiple countries)

**Why not World ID?**
- Requires physical Orb visit for verification
- Primarily Proof of Personhood only (no extensibility to other credential types)
- Regulatory risk: banned or suspended in Brazil, Philippines, Thailand, Indonesia
- Protocol in transition (v3 → v4) with breaking changes
- Application developer fees being introduced (amounts undisclosed)

**Why not a custom implementation?**
- ZKP circuits require specialized cryptographic expertise
- Auditing and security burden falls entirely on PEACE COIN
- No ecosystem of credential issuers to leverage

**Why register on PCEToken rather than each PCECommunityToken?**
Identity is a property of a person, not of a community. Requiring registration per community token would create unnecessary friction (N transactions for N communities) and duplicate storage. Centralizing identity on PCEToken allows one registration to apply protocol-wide, while each community token independently enforces its own daily mint limits.

**Why allow multiple wallets per identity?**
Users have legitimate reasons to use multiple wallets (operational security, role separation, privacy). Prohibiting this would harm user experience without improving Sybil resistance. Instead, the per-identity cap removes the economic incentive to split across wallets while preserving user freedom.

**Why a bonus rather than a requirement?**
Making verification mandatory would exclude users in regions where Privado ID issuers are unavailable. A bonus-based approach creates a gradual incentive to verify without hard-gating participation.

## Notes (Optional)

- Related implementation: https://github.com/peacecoin-protocol/core/pull/TBD
- Privado ID UniversalVerifier: `0xfcc86A79fCb057A8e55C6B853dff9479C3cf607c` (all EVM chains)
- Privado ID documentation: https://docs.privado.id/
- The `identityNullifier` is derived from the user's Privado ID identity commitment and the PEACE COIN-specific action identifier. It is deterministic: the same identity always produces the same nullifier for PEACE COIN, but a different nullifier for other applications.
- The credential schema for Proof of Personhood and the trusted Issuer list should be determined through governance before implementation.

## 6. Copyright and License

Copyright and related rights waived via [CC0](https://creativecommons.org/publicdomain/zero/1.0/).
