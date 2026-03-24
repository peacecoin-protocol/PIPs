---
pip: 15
title: "Message Count Parameter for Arigato Creation in Transfers"
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

## 2. Specification / Details

### New Functions

Four new public functions are added to `PCECommunityToken`, each mirroring an existing transfer function but accepting an additional `messageCount` parameter:

#### 2.1 `transferWithMessageCount`

```solidity
function transferWithMessageCount(
    address receiver,
    uint256 displayAmount,
    uint256 messageCount
) public returns (bool);
```

Behaves identically to `transfer`, except `messageCount` is passed to `_mintArigatoCreation` instead of the hardcoded `1`.

#### 2.2 `transferFromWithMessageCount`

```solidity
function transferFromWithMessageCount(
    address sender,
    address receiver,
    uint256 displayBalance,
    uint256 messageCount
) public returns (bool);
```

Behaves identically to `transferFrom`, except `messageCount` is passed to `_mintArigatoCreation`.

#### 2.3 `transferWithAuthorizationWithMessageCount`

```solidity
function transferWithAuthorizationWithMessageCount(
    address from,
    address to,
    uint256 displayAmount,
    uint256 validAfter,
    uint256 validBefore,
    bytes32 nonce,
    uint8 v,
    bytes32 r,
    bytes32 s,
    uint256 messageCount
) public;
```

Behaves identically to `transferWithAuthorization`, except:
- `messageCount` is passed to `_mintArigatoCreation`.
- A **new EIP-712 typehash** is used for signature verification that includes the `messageCount` field, preventing third parties from tampering with the value.

**EIP-712 Type:**
```
TransferWithAuthorizationWithMessageCount(address from,address to,uint256 value,uint256 validAfter,uint256 validBefore,bytes32 nonce,uint256 messageCount)
```

#### 2.4 `transferFromWithAuthorizationWithMessageCount`

```solidity
function transferFromWithAuthorizationWithMessageCount(
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
    uint256 messageCount
) public;
```

Behaves identically to `transferFromWithAuthorization`, except:
- `messageCount` is passed to `_mintArigatoCreation`.
- A **new EIP-712 typehash** is used that includes the `messageCount` field.

**EIP-712 Type:**
```
TransferFromWithAuthorizationWithMessageCount(address spender,address from,address to,uint256 value,uint256 validAfter,uint256 validBefore,bytes32 nonce,uint256 messageCount)
```

### Unchanged Behavior

- Existing `transfer`, `transferFrom`, `transferWithAuthorization`, and `transferFromWithAuthorization` remain **unchanged** and continue to pass `1` to `_mintArigatoCreation`. No breaking changes.
- The internal `_mintArigatoCreation` function is not modified.
- `messageCount` is clamped by the existing `MAX_CHARACTER_LENGTH` constant (`10`) inside `_mintArigatoCreation` — values above 10 are treated as 10.

### New Events

```solidity
event TransferWithMessageCount(
    address indexed from,
    address indexed to,
    uint256 displayAmount,
    uint256 messageCount
);
```

Emitted by all four new functions to allow indexers to track message-count-aware transfers.

## 3. Impact and Compatibility

**Backwards Compatibility:** Fully backwards compatible. Existing functions are untouched. Existing callers (wallets, relayers) require no changes.

**Storage:** No new storage variables. No storage layout changes.

**Contract Size:** Four new functions increase bytecode. Given the existing contract is close to the EVM size limit, this should be monitored during implementation.

**Front-end Impact:** Applications that wish to leverage message-count-aware Arigato Creation must call the new functions. Standard ERC-20 `transfer`/`transferFrom` calls continue to work with `messageCount=1`.

**Relayer Impact:** Relayers supporting the new `WithAuthorization` variants must construct signatures using the new EIP-712 typehashes.

## 4. Rationale and Alternatives

**Why new functions instead of modifying existing ones?**
Changing the signature of `transfer(address, uint256)` would break ERC-20 compatibility. Changing `transferWithAuthorization` would break existing relayer integrations and invalidate any pre-signed authorizations. New functions preserve full backwards compatibility.

**Why not use a storage-based setter (e.g., `setMessageCount` then `transfer`)?**
A two-step pattern is vulnerable to front-running: an attacker could call `transfer` between the user's `setMessageCount` and `transfer` calls, consuming the stored value. It also requires two transactions in the non-meta-transaction case. A single-function approach is atomic and simpler.

**Why include `messageCount` in the EIP-712 signature?**
Without it, a relayer or front-runner could alter the `messageCount` value after the user signs the authorization. Including it in the signed data ensures only the signer can determine the message count.

## Notes (Optional)

- Related implementation: https://github.com/peacecoin-protocol/core/pull/TBD
- Requires: None
- The `messageCount` parameter represents the number of message characters associated with the transfer, as defined by the existing Arigato Creation algorithm. Values of `0` are treated as `1` by `_mintArigatoCreation`.

## 5. Copyright and License

Copyright and related rights waived via [CC0](https://creativecommons.org/publicdomain/zero/1.0/).
