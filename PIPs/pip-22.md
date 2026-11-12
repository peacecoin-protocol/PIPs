---
pip: 22
title: Smart-Account Signatures and Signed Swaps for Community Tokens and PCE Token
proposer: CHIBA Masahiro (@nihen)
status: Draft
type: Core
created: 2026-11-09
requires: [17]
replaces: []
---

## PIP-22: Smart-Account Signatures and Signed Swaps for Community Tokens and PCE Token

### Abstract

This proposal lets smart accounts (ERC-1271 contract wallets, including accounts that are not yet deployed, via ERC-6492) authorize community-token transfers, infinity-approve flag changes, voucher claims and token swaps by signature, so a relayer can submit them on the account's behalf. It adds `bytes`-signature overloads of the existing authorization functions, adds signed versions of the three swap directions (community → community, community → PCE, PCE → community), lets every new signed operation bind a maximum relayer fee (`maxFee`) and, for swaps, a minimum output (`minAmountOut`), and emits a new `AuthorizationExecuted` event that ties each consumed nonce to the signed struct hash.

### Motivation

Community-token signature authorizations (EIP-3009 `transferWithAuthorization`, `setInfinityApproveFlagWithAuthorization`, `claimVoucherWithAuthorization`) currently verify signatures with `ecrecover` only. Only EOAs can use them. Smart accounts — for example the passkey-based Kernel v3.3 accounts used by PEACE COIN Passport — cannot sign a gasless transfer or voucher claim, and swaps have no signed form at all, so a smart account must hold native gas or route every swap through a bundler.

Three further gaps exist in the current signed flows:

1. The relayer fee is read from the token at execution time and is not covered by the signature. A signer cannot bound what the relayer will charge.
2. The signed voucher claim withholds the relayer fee from the claimed amount, but the signature does not bound that fee either.
3. Off-chain systems that index authorizations see only `AuthorizationUsed(authorizer, nonce)`. They cannot tell which signed request a nonce belongs to without re-deriving it from calldata.

### Specification

The key words "MUST", "MUST NOT", "SHOULD" and "MAY" are to be interpreted as described in RFC 2119.

#### Signature check

A new internal library `AuthorizationSignature` provides `isValid(address signer, bytes32 digest, bytes signature) returns (bool)`. It MUST evaluate, in this order:

1. **ERC-6492 wrapper.** If `signature.length >= 32` and its last 32 bytes equal `0x6492649264926492649264926492649264926492649264926492649264926492`, decode the remainder as `abi.encode(address factory, bytes factoryCalldata, bytes innerSignature)`.
   - If `signer` has no code, call `factory` with `factoryCalldata` through the calling token's `ERC6492FactoryCaller` (below). The result of the call is ignored. If `signer` still has no code afterwards, return `false`.
   - Return the ERC-1271 result for `innerSignature` (`SignatureChecker.isValidERC1271SignatureNow`, a `staticcall` to `isValidSignature`). There MUST NOT be an ECDSA fallback for a wrapped signature.
2. **ECDSA.** If `ECDSA.tryRecover(digest, signature)` returns `signer` without error, return `true`. This keeps EOAs, including EIP-7702 delegated EOAs that have code, working with 65-byte signatures.
3. **ERC-1271.** If `signer` has no code, return `false`. Otherwise return the ERC-1271 result for `signature`.

`ERC6492FactoryCaller` is a stateless contract with one function, `callFactory(address factory, bytes factoryCalldata)`, which performs `factory.call(factoryCalldata)` and ignores the result. Each token that verifies signatures uses its own caller, deployed lazily with CREATE2 from the token's address with salt `bytes32(0)`. The caller MUST NOT be granted any role, allowance or balance.

#### Signing domains

- `PCECommunityToken` `bytes` overloads: EIP-712 domain `{name: "PeaceBaseCoin", version: "1", chainId, verifyingContract: <community token>}`. This is the same domain used by the existing `v, r, s` community-token authorizations.
- `PCEToken.swapToLocalTokenWithAuthorization`: EIP-712 domain `{name: "PCE Token", version: "1", chainId, verifyingContract: <PCEToken proxy>}`. This is separate from the salt-based domain used by `PCEToken`'s native meta-transactions.

#### Interface — PCECommunityToken (new overloads)

```solidity
function transferWithAuthorization(
    address from, address to, uint256 displayAmount, uint256 maxFee,
    uint256 validAfter, uint256 validBefore, bytes32 nonce, bytes memory signature
) external;

function setInfinityApproveFlagWithAuthorization(
    address owner, address spender, bool flag, uint256 maxFee,
    uint256 validAfter, uint256 validBefore, bytes32 nonce, bytes memory signature
) external;

function claimVoucherWithAuthorization(
    address claimer, string memory issuanceId, string memory code, bytes32[] calldata proof, uint256 maxFee,
    uint256 validAfter, uint256 validBefore, bytes32 nonce, bytes memory signature
) external;

function swapTokensWithAuthorization(
    address from, address toToken, uint256 value, uint256 minAmountOut, uint256 maxFee,
    uint256 validAfter, uint256 validBefore, bytes32 nonce, bytes memory signature
) external;

function swapToPCEWithAuthorization(
    address from, uint256 value, uint256 minAmountOut, uint256 maxFee,
    uint256 validAfter, uint256 validBefore, bytes32 nonce, bytes memory signature
) external;
```

Type hashes (`keccak256` of the strings below), exposed as public constants:

| Constant | Type string |
|---|---|
| `TRANSFER_WITH_FEE_AUTHORIZATION_TYPEHASH` | `TransferWithFeeAuthorization(address from,address to,uint256 value,uint256 maxFee,uint256 validAfter,uint256 validBefore,bytes32 nonce)` |
| `SET_INFINITY_APPROVE_FLAG_WITH_FEE_AUTHORIZATION_TYPEHASH` | `SetInfinityApproveFlagWithFeeAuthorization(address owner,address spender,bool flag,uint256 maxFee,uint256 validAfter,uint256 validBefore,bytes32 nonce)` |
| `CLAIM_WITH_FEE_AUTHORIZATION_TYPEHASH` | `ClaimWithFeeAuthorization(address claimer,string issuanceId,string code,uint256 maxFee,uint256 validAfter,uint256 validBefore,bytes32 nonce)` |
| `SWAP_TOKENS_WITH_AUTHORIZATION_TYPEHASH` | `SwapTokensWithAuthorization(address from,address toToken,uint256 value,uint256 minAmountOut,uint256 maxFee,uint256 validAfter,uint256 validBefore,bytes32 nonce)` |
| `SWAP_TO_PCE_WITH_AUTHORIZATION_TYPEHASH` | `SwapToPCEWithAuthorization(address from,uint256 value,uint256 minAmountOut,uint256 maxFee,uint256 validAfter,uint256 validBefore,bytes32 nonce)` |

In `ClaimWithFeeAuthorization`, `issuanceId` and `code` are encoded as `keccak256(bytes(...))`, as EIP-712 requires for `string`.

#### Interface — PCEToken (new)

```solidity
bytes32 public constant SWAP_TO_LOCAL_TOKEN_WITH_AUTHORIZATION_TYPEHASH = keccak256(
    "SwapToLocalTokenWithAuthorization(address from,address toToken,uint256 value,uint256 minAmountOut,uint256 maxFee,uint256 validAfter,uint256 validBefore,bytes32 nonce)"
);

function swapToLocalTokenWithAuthorization(
    address from, address toToken, uint256 value, uint256 minAmountOut, uint256 maxFee,
    uint256 validAfter, uint256 validBefore, bytes32 nonce, bytes memory signature
) external;

/// Callable only by a registered community token (msg.sender), for its signed swap to PCE.
function swapFromLocalTokenFor(address account, uint256 amountToSwap) external;

function authorizationState(address authorizer, bytes32 nonce) external view returns (bool);
```

#### Algorithm — common to every new signed function

1. If the authorizer is `address(0)`, revert (`"Invalid authorizer address"`).
2. Require `validAfter < block.timestamp < validBefore` (`"Not yet valid"`, `"Authorization expired"`).
3. Require the `(authorizer, nonce)` pair is unused (`"Authorization used"`), then mark it used and emit `AuthorizationUsed` and `AuthorizationExecuted`. The nonce MUST be consumed before the signature is checked.
4. Compute `digest = keccak256("\x19\x01" || domainSeparator || structHash)` and require `AuthorizationSignature.isValid(authorizer, digest, signature)` (`"Invalid signature"`).
5. Only after step 4, update factors and read balances, fees and exchange rates. This MUST hold because step 4 can execute arbitrary code (the ERC-6492 factory call or the wallet's `isValidSignature`).

Every new signed function, and every existing `v, r, s` signed function of `PCECommunityToken` (including `transferFromWithAuthorization`), MUST be `nonReentrant` (OpenZeppelin `ReentrancyGuardTransient`, EIP-1153 transient storage).

#### Algorithm — fees and output bounds

- **Community-token relayer fee.** The fee is `getMetaTransactionFee()` of the community token, in display units. If it exceeds `maxFee`, revert (`"Fee exceeds maxFee"`). It is collected from `from` in the community token and paid to the relayer (`msg.sender`) in PCE through the existing `swapFeeFromLocalToken` path. A zero fee is not collected.
- **Transfer and flag change.** The fee is charged in addition to the transferred amount.
- **Voucher claim.** The fee is withheld from the claimed amount, as in the existing signed claim, and is capped by `maxFee`.
- **`swapTokensWithAuthorization`.** Perform the PIP-17 inter-community swap for `from` (the daily swap-to-PCE limits do not apply). Require that `from`'s display balance of `toToken` increased by at least `minAmountOut` (`"Insufficient output amount"`). Then collect the relayer fee in the source token.
- **`swapToPCEWithAuthorization`.** Call `PCEToken.swapFromLocalTokenFor(from, value)`, which runs the same logic as `swapFromLocalToken` for `from`. Require that `from`'s PCE balance increased by at least `minAmountOut`. Then collect the relayer fee in the source token.
- **`swapToLocalTokenWithAuthorization`.** Require `toToken` is registered (`"Target token not found"`). The fee is `PCEToken.getMetaTransactionFee()` in PCE. Require fee `<= maxFee` and `balanceOf(from) >= value + fee`. Run the `swapToLocalToken` logic for `from`, require the `toToken` balance increase `>= minAmountOut`, then transfer the fee in PCE from `from` to `msg.sender`.

#### Events

```solidity
event AuthorizationExecuted(address indexed authorizer, bytes32 indexed nonce, bytes32 structHash);
```

`AuthorizationExecuted` MUST be emitted together with `AuthorizationUsed` whenever an authorization nonce is consumed: by the new functions, by the existing community-token `v, r, s` authorizations, by the EIP-3009 transfer path, and by the voucher claim path. `structHash` is the EIP-712 struct hash that was signed.

#### Storage

- `PCEToken`: one mapping `_authorizationStates` (`mapping(address => mapping(bytes32 => bool))`) appended after the existing storage.
- `PCECommunityToken`: no new persistent storage. The reentrancy guard uses transient storage.

#### Versions

`PCEToken.version()` becomes `1.0.18` and `PCECommunityToken.version()` becomes `1.0.20` (together with PIP-17).

### Rationale

**Why accept ERC-1271 at all?** Smart accounts cannot produce an ECDSA signature for their own address. ERC-1271 is the standard way for a contract to state that it approves a hash. Without it, the gasless flows of the community tokens are unavailable to smart-account users.

**Why try ECDSA before ERC-1271?** An EIP-7702 delegated EOA has code but can still sign with its key. Checking ECDSA first keeps such EOAs working and avoids an external call for ordinary EOAs.

**Why new `bytes` overloads instead of changing the existing functions?** The existing `v, r, s` functions are in use by deployed clients. Overloads keep their ABI and selectors unchanged. ERC-1271 signatures can be longer than 65 bytes, so they cannot be passed as `v, r, s`.

**Why sign `maxFee`?** The relayer fee depends on the base fee and the PCE/native rate at execution time. Without a signed cap, a signer cannot bound the cost. The existing `v, r, s` functions keep their current behavior (no cap) for compatibility.

**Why include `maxFee` in the signed claim, unlike the issue text?** The original request (core#49) described voucher claims as fee-free. The implementation withholds the relayer fee from the claimed amount, so the claim needs the same cap as the other signed operations.

**Why sign `minAmountOut` on swaps?** Factor decay, exchange-rate changes and the fee can change the output between signing and execution. `minAmountOut` is checked against the actual balance change, which covers all of these.

**Why support ERC-6492?** An ERC-1271 signature can only be verified once the account exists. Passport accounts are deployed lazily, on first use. ERC-6492 lets the first signed operation deploy the account and use it in one transaction. The alternative — having the relayer call the account factory first — needs a second transaction and depends on the factory being callable by the relayer.

**Why a separate factory caller?** The factory address and calldata come from the signature wrapper and are not covered by the signature. If the token itself made the call, the factory would act with the token's identity (for example, calling `PCEToken` functions that only registered community tokens may call). The stateless `ERC6492FactoryCaller` has no privileges, so the call cannot do anything the factory could not do on its own.

**Why a separate domain for `PCEToken`?** `PCEToken` already uses a salt-based EIP-712 domain for native meta-transactions. A distinct `{name: "PCE Token", version: "1"}` domain prevents a signature for one mechanism from being valid in the other.

### Backwards Compatibility

- All existing functions keep their signatures and selectors. The existing `v, r, s` authorizations of `PCECommunityToken` behave as before, except that they are now `nonReentrant` and also emit `AuthorizationExecuted`.
- `PCEToken` storage gains one mapping after the existing layout. `PCECommunityToken` storage is unchanged. OpenZeppelin upgrade validation against `v18` passes for both contracts.
- Clients that call functions by name MUST select the intended overload explicitly (for example by full signature), because `transferWithAuthorization`, `setInfinityApproveFlagWithAuthorization` and `claimVoucherWithAuthorization` now have two forms each.
- The `PCECommunityToken` runtime code is about 28.4 KB. This exceeds the EIP-170 limit of 24 KB but is within Polygon PoS's 32 KB limit (PIP-30). The implementation has been deployed on Polygon mainnet.

### Test Cases

Cases 1–12 are covered by the `peacecoin-protocol/core` test suite at `v20` (199 tests in total; mainly `test/BytesSignatureAuthorization.t.sol` and `test/ERC6492Signature.t.sol`). The amounts in cases 1 and 2 are illustrative. Amounts are display units with 18 decimals; factors and exchange rates are `1e18` unless noted.

1. **ERC-1271 transfer.** A contract wallet `W` holds 100 tokens. The relayer fee is 0.5 and `W` signs `TransferWithFeeAuthorization(W, bob, 10, maxFee = 1, ...)`. Result: `bob` receives 10, `W` loses 10.5 (in raw terms, rounded up for the fee), and the relayer receives the PCE value of 0.5.
2. **Fee above cap.** As case 1 with the fee raised to 2. Result: revert `"Fee exceeds maxFee"`.
3. **Replay.** Resubmitting the authorization from case 1. Result: revert `"Authorization used"`.
4. **Wrong signer.** `W.isValidSignature` returns a non-magic value. Result: revert `"Invalid signature"`.
5. **EOA through the `bytes` overload.** A 65-byte ECDSA signature by an EOA. Result: accepted.
6. **ERC-6492, undeployed account.** The signature wraps a factory call that deploys `W` at the signer address. Result: `W` is deployed and the transfer executes in the same transaction.
7. **ERC-6492, factory does not deploy.** The factory call reverts or deploys a different address. Result: revert `"Invalid signature"`. No ECDSA fallback is attempted.
8. **ERC-6492, reentrant factory.** The factory calls back into a signed function of the same token. Result: revert `ReentrancyGuardReentrantCall`. The inner nonce remains unused.
9. **Reads after verification.** A factory that changes the signer's balance or the token's factor during verification. Result: the operation uses the post-verification values (balance, factor, fee, `minAmountOut` comparison).
10. **Signed inter-community swap.** `from` signs `SwapTokensWithAuthorization(from, B, 30, minAmountOut, maxFee, ...)`. Result: the output and deposit movement equal those of PIP-17 `swapTokens(B, 30)`. If the output is below `minAmountOut`, revert `"Insufficient output amount"`.
11. **Signed PCE → community swap.** `from` holds `value + fee` PCE. Result: `from` receives the community token, the relayer receives `fee` PCE. (The `"Not enough PCEToken balance"` revert for a balance below `value + fee` is specified above but has no dedicated test.)
12. **Domain separation.** A signature produced with `PCEToken`'s native meta-transaction domain is presented to `swapToLocalTokenWithAuthorization`. Result: revert `"Invalid signature"`.
13. **Kernel v3.3 (Polygon fork, outside the repository).** An account created by the Kernel v3.3 factory with the WebAuthn validator signs with its passkey. Result: accepted. A signature from a different passkey, or an unwrapped (non-Kernel-formatted) signature, is rejected. An ERC-6492 signature that calls `createAccount` deploys the account and executes the transfer in one transaction.

### Security Considerations

- **The wallet decides what a valid signature is.** With ERC-1271, the token accepts whatever the account's `isValidSignature` accepts. For Kernel accounts, this includes any validator installed on the account, not only the passkey. Wallet integrators MUST treat validator installation as equivalent to granting signing power over the account's community tokens.
- **Arbitrary external code during verification.** `isValidSignature` (a `staticcall`) and the ERC-6492 factory (a normal `call`) run untrusted code in the middle of the operation. The implementation MUST consume the nonce before verification, MUST be `nonReentrant`, and MUST read balances, factors, exchange rates and fees only after verification. Tests 8 and 9 cover this.
- **Factory call privileges.** The factory is called only through `ERC6492FactoryCaller`, which has no role, allowance or balance. Governance MUST NOT grant it any.
- **No ECDSA fallback for wrapped signatures.** If an ERC-6492 wrapper fails to produce code at the signer address, the signature is rejected. This prevents a wrapper from being used to downgrade verification.
- **Hash binding.** The EIP-712 digest includes the `verifyingContract`, so a signature for one token cannot be replayed on another. Smart accounts SHOULD additionally bind the hash to themselves (Kernel does this with its own EIP-712 wrapper) so that one signature cannot authorize two accounts that share an owner.
- **Relayer behavior.** A relayer can still delay or withhold a signed request until `validBefore`. Signers SHOULD use short validity windows. `maxFee` and `minAmountOut` bound the cost and the output, not the timing.
- **Upgrade impact.** The change is delivered through the community-token beacon and affects every community token at once. It was validated on DEV and rehearsed on a Polygon mainnet fork before this proposal.

### Notes

- **Origin:** `peacecoin-protocol/core` issue #49. Implementation: core PRs #51 (ERC-1271, `maxFee`, signed swaps, `AuthorizationExecuted`) and #52 (ERC-6492 and the reentrancy guard), tag `v20`. PIP-17 (core PRs #50 and #53) is a prerequisite.
- **Out of scope:**
  - Adding `maxFee` to the existing `v, r, s` functions. Their behavior is kept for compatibility.
  - Native meta-transactions of `PCEToken` (`executeMetaTransaction`). They are unchanged and remain ECDSA-only.
  - Wallet-side policy, such as which validators a Passport account may install. This is a wallet design decision, not a protocol rule.
- **Client integration:** bc-core-api (web3.js v4) and pce_web (viem) need to call the new overloads by full signature. This has not yet been confirmed in those clients.
