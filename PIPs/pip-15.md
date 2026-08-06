---
pip: 15
title: "Message Count and Attachment Flags for Arigato Creation in Transfers"
proposer: "CHIBA Masahiro (@nihen)"
status: "Draft"
type: "Core"
created: "2026-03-25"
requires: []
replaces: []
---

## 1. Motivation

The Arigato Creation algorithm in `PCECommunityToken` already accepts a `messageCharacters` parameter that influences the mint calculation — specifically, the penalty curve applied when the sender's usage rate deviates from the optimal rate. However, all current transfer functions (`transfer`, `transferFrom`, `transferWithAuthorization`, `transferFromWithAuthorization`) hardcode this value to `1`.

This means the Arigato Creation incentive curve cannot differentiate between a bare token transfer and a transfer accompanied by a meaningful message (e.g., a gratitude note in a community app). Unlocking this parameter allows front-end applications to pass the actual message count, enabling the protocol to reward richer social interactions as originally designed.

Additionally, modern community applications allow users to attach voice messages or images to gratitude transfers. These rich media attachments represent a higher level of social engagement. By introducing `hasVoiceAttachment` and `hasImageAttachment` flags and reducing the usage-rate deviation penalty for transfers with attachments, the protocol incentivizes more meaningful interactions.

## 2. Specification / Details

### New Functions

Four new public functions are added to `PCECommunityToken`, each mirroring an existing transfer function but accepting additional `messageCount`, `hasVoiceAttachment`, and `hasImageAttachment` parameters:

#### 2.1 `transferWithMetadata`

```solidity
function transferWithMetadata(
    address receiver,
    uint256 displayAmount,
    uint256 messageCount,
    bool hasVoiceAttachment,
    bool hasImageAttachment
) public returns (bool);
```

Behaves identically to `transfer`, except `messageCount` is passed to `_mintArigatoCreation` instead of the hardcoded `1`, and attachment flags are applied to reduce the deviation penalty.

#### 2.2 `transferFromWithMetadata`

```solidity
function transferFromWithMetadata(
    address sender,
    address receiver,
    uint256 displayBalance,
    uint256 messageCount,
    bool hasVoiceAttachment,
    bool hasImageAttachment
) public returns (bool);
```

Behaves identically to `transferFrom`, except `messageCount` is passed to `_mintArigatoCreation` and attachment flags are applied.

#### 2.3 `transferWithAuthorizationAndMetadata`

```solidity
function transferWithAuthorizationAndMetadata(
    address from,
    address to,
    uint256 displayAmount,
    uint256 validAfter,
    uint256 validBefore,
    bytes32 nonce,
    uint8 v,
    bytes32 r,
    bytes32 s,
    uint256 messageCount,
    bool hasVoiceAttachment,
    bool hasImageAttachment
) public;
```

Behaves identically to `transferWithAuthorization`, except:
- `messageCount` is passed to `_mintArigatoCreation`.
- Attachment flags are applied to reduce the deviation penalty.
- A **new EIP-712 typehash** is used for signature verification that includes all metadata fields, preventing third parties from tampering with the values.

**EIP-712 Type:**
```
TransferWithAuthorizationAndMetadata(address from,address to,uint256 value,uint256 validAfter,uint256 validBefore,bytes32 nonce,uint256 messageCount,bool hasVoiceAttachment,bool hasImageAttachment)
```

#### 2.4 `transferFromWithAuthorizationAndMetadata`

```solidity
function transferFromWithAuthorizationAndMetadata(
    address spender,
    address from,
    address to,
    uint256 displayAmount,
    uint256 validAfter,
    uint256 validBefore,
    bytes32 nonce,
    uint8 v,
    bytes32 r,
    bytes32 s,
    uint256 messageCount,
    bool hasVoiceAttachment,
    bool hasImageAttachment
) public;
```

Behaves identically to `transferFromWithAuthorization`, except:
- `messageCount` is passed to `_mintArigatoCreation`.
- Attachment flags are applied to reduce the deviation penalty.
- A **new EIP-712 typehash** is used that includes all metadata fields.

**EIP-712 Type:**
```
TransferFromWithAuthorizationAndMetadata(address spender,address from,address to,uint256 value,uint256 validAfter,uint256 validBefore,bytes32 nonce,uint256 messageCount,bool hasVoiceAttachment,bool hasImageAttachment)
```

### Attachment Penalty Reduction

Each attachment flag reduces the usage-rate deviation penalty (`changeMulBp`). The reduction rates are **configurable per community token** via setter functions, with the following protocol-wide defaults:

```
DEFAULT_VOICE_REDUCTION_BP = 1000   // 10%
DEFAULT_IMAGE_REDUCTION_BP = 1000   // 10%
```

#### Per-Token Configuration

Each `PCECommunityToken` stores its own reduction rates. If not explicitly set, the default values above are used.

```solidity
// Storage
uint256 public voiceReductionBp;   // initialized to DEFAULT_VOICE_REDUCTION_BP
uint256 public imageReductionBp;   // initialized to DEFAULT_IMAGE_REDUCTION_BP

// Setter (owner only)
function setAttachmentReductionBp(
    uint256 _voiceReductionBp,
    uint256 _imageReductionBp
) external onlyOwner;
```

**Constraints:**
- Each value must be in the range `[0, 5000]` (0% to 50%).
- The sum `voiceReductionBp + imageReductionBp` must not exceed `5000` (50%), ensuring the penalty is never reduced by more than half.

#### Penalty Reduction Formula

```
attachmentReductionBp = 0
if hasVoiceAttachment: attachmentReductionBp += voiceReductionBp
if hasImageAttachment: attachmentReductionBp += imageReductionBp

effectiveChangeMulBp = changeMulBp × (BP_BASE - attachmentReductionBp) / BP_BASE

increaseBp = maxIncreaseBp - (effectiveChangeMulBp × messageBp) / BP_BASE
```

#### Example with Default Values (10% each)

| Attachments | Penalty Reduction | Effect |
|-------------|-------------------|--------|
| None | 0% | `effectiveChangeMulBp = changeMulBp` (unchanged) |
| Voice only | 10% | `effectiveChangeMulBp = changeMulBp × 9000 / 10000` |
| Image only | 10% | `effectiveChangeMulBp = changeMulBp × 9000 / 10000` |
| Voice + Image | 20% | `effectiveChangeMulBp = changeMulBp × 8000 / 10000` |

A transfer with both voice and image attachments has its deviation penalty reduced by 20%, resulting in a larger `increaseBp` and more Arigato Creation minted.

### Unchanged Behavior

- Existing `transfer`, `transferFrom`, `transferWithAuthorization`, and `transferFromWithAuthorization` remain **unchanged** and continue to pass `1` to `_mintArigatoCreation` with no attachment reduction. No breaking changes.
- `messageCount` is clamped by the existing `MAX_CHARACTER_LENGTH` constant (`10`) inside `_mintArigatoCreation` — values above 10 are treated as 10.

### New Events

```solidity
event TransferWithMetadata(
    address indexed from,
    address indexed to,
    uint256 displayAmount,
    uint256 messageCount,
    bool hasVoiceAttachment,
    bool hasImageAttachment
);

event AttachmentReductionBpUpdated(
    uint256 voiceReductionBp,
    uint256 imageReductionBp
);
```

`TransferWithMetadata` is emitted by all four new functions to allow indexers to track metadata-aware transfers. `AttachmentReductionBpUpdated` is emitted when the owner changes the reduction rates.

## 3. Impact and Compatibility

**Backwards Compatibility:** Fully backwards compatible. Existing functions are untouched. Existing callers (wallets, relayers) require no changes.

**Storage:** Two new `uint256` storage variables (`voiceReductionBp`, `imageReductionBp`) are added per community token. These are initialized to the default values during deployment/upgrade.

**Contract Size:** Four new functions and a setter increase bytecode. Given the existing contract is close to the EVM size limit, this should be monitored during implementation.

**Front-end Impact:** Applications that wish to leverage metadata-aware Arigato Creation must call the new functions. Standard ERC-20 `transfer`/`transferFrom` calls continue to work with `messageCount=1` and no attachment reduction.

**Relayer Impact:** Relayers supporting the new `WithAuthorization` variants must construct signatures using the new EIP-712 typehashes.

**Governance Impact:** Community token owners can tune penalty reduction rates to match their community's engagement patterns without requiring a contract upgrade.

## 4. Rationale and Alternatives

**Why new functions instead of modifying existing ones?**
Changing the signature of `transfer(address, uint256)` would break ERC-20 compatibility. Changing `transferWithAuthorization` would break existing relayer integrations and invalidate any pre-signed authorizations. New functions preserve full backwards compatibility.

**Why not use a storage-based setter (e.g., `setMessageCount` then `transfer`)?**
A two-step pattern is vulnerable to front-running: an attacker could call `transfer` between the user's `setMessageCount` and `transfer` calls, consuming the stored value. It also requires two transactions in the non-meta-transaction case. A single-function approach is atomic and simpler.

**Why include metadata fields in the EIP-712 signature?**
Without it, a relayer or front-runner could alter the `messageCount` or attachment flags after the user signs the authorization. Including them in the signed data ensures only the signer can determine the values.

**Why boolean flags instead of an attachment count or enum?**
Boolean flags are self-documenting in ABI, easy for front-end developers to use, and EVM-efficient (packed into a single slot). They clearly convey presence/absence of each attachment type.

**Why configurable penalty reduction rates per community token?**
Different communities have different engagement patterns. A community focused on visual art may want a higher image attachment reduction, while a voice-chat-centric community may want to weight voice attachments more heavily. Per-token configuration allows each community to tune incentives without protocol-level changes. The protocol-wide defaults (10% each) provide a sensible starting point.

**Why cap the total reduction at 50%?**
The penalty curve is a core mechanism for maintaining healthy usage-rate distribution. Allowing reductions beyond 50% would risk making the penalty ineffective, potentially enabling gaming of the Arigato Creation algorithm.

## Notes (Optional)

- Related implementation: https://github.com/peacecoin-protocol/core/pull/34
- Requires: None
- The `messageCount` parameter represents the number of message characters associated with the transfer, as defined by the existing Arigato Creation algorithm. Values of `0` are treated as `1` by `_mintArigatoCreation`.
- `hasVoiceAttachment` and `hasImageAttachment` each apply their respective configurable penalty reduction. They stack additively (voice + image = sum of both reduction rates).
- Community token owners can call `setAttachmentReductionBp` at any time to adjust rates. Setting both to `0` effectively disables the attachment reduction feature for that token.

## 5. Copyright and License

Copyright and related rights waived via [CC0](https://creativecommons.org/publicdomain/zero/1.0/).
