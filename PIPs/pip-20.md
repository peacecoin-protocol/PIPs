---
pip: 20
title: Voucher-backed Discount Payments for Community Tokens
proposer: CHIBA Masahiro (@nihen)
status: Draft
type: Core
created: 2026-05-29
requires: [13]
replaces: []
---

## PIP-20: Voucher-backed Discount Payments for Community Tokens

### Abstract

This proposal extends the existing community-token voucher mechanism so a voucher can be redeemed directly inside a merchant payment instead of being claimed as spendable balance by the user. A voucher issuance continues to represent locked community-token funds, a Merkle root of claim codes, a fixed `amountPerClaim`, per-user claim limits, total limits, and a validity window; the new payment flow sends the voucher amount to the merchant as a sponsor-funded discount while the buyer pays only the remaining amount.

The extension deliberately avoids adding more `transferWithAuthorizationWith...` variants to `PCECommunityToken`. Instead, it adds one voucher-specific EIP-712 authorization that binds the buyer, merchant, issuance, code, payment amount, relayer, and nonce to a single atomic checkout.

### Motivation

The current voucher mechanism is a funded claim system. An issuance locks community tokens inside the `PCECommunityToken` contract and lets a user prove possession of a claim code using a Merkle proof. On success, the current `claimVoucher` path transfers `amountPerClaim` from the contract to the claimer.

That is appropriate for token grants, but not for merchant discounts. In a merchant checkout, the desired settlement is:

```text
paymentAmount = userPaidAmount + discountAmount
buyer -> merchant: userPaidAmount
voucher locked funds -> merchant: discountAmount
```

For example, if a merchant charges 1,000 CT and the voucher amount is 200 CT, the buyer should pay 800 CT and the voucher pool should pay 200 CT. The merchant receives the full 1,000 CT in the same transaction.

Using the current claim flow requires two separate steps: first claim the voucher to the buyer, then pay the merchant. That has three issues:

1. The voucher can be claimed without any associated purchase.
2. The merchant payment and sponsor subsidy are not atomic.
3. Relayer and wallet integrations would be pushed toward adding more transfer variants such as `transferWithAuthorizationWithVoucher` or combinations with meta-transaction fee handling.

PIP-20 changes the voucher redemption target for checkout use cases: the voucher amount goes directly to the merchant, and the buyer's signed authorization covers the whole payment context.

### Specification

#### Existing Concepts Reused

The proposal builds on the current integrated voucher model in `PCECommunityToken` / `VoucherSystem`:

```solidity
struct VoucherIssuance {
    string issuanceId;
    address owner;
    string name;
    uint256 amountPerClaim;
    uint256 countLimitPerUser;
    uint256 totalAmountLimit;
    uint256 startTime;
    uint256 endTime;
    bytes32 merkleRoot;
    bool isActive;
    string ipfsCid;
}
```

The following existing state remains authoritative:

```solidity
mapping(string issuanceId => VoucherIssuance issuance) issuances;
mapping(string issuanceId => mapping(address user => uint256 claimCount)) claimCountPerUser;
mapping(string issuanceId => mapping(string issueCode => bool isUsed)) isCodeUsed;
mapping(string issuanceId => uint256 remainingRawAmount) remainingRawAmount;
mapping(string issuanceId => uint256 claimedRawAmount) claimedRawAmount;
mapping(string issuanceId => uint256 claimedDisplayAmount) claimedDisplayAmount;
mapping(string issuanceId => uint256 totalClaimCount) totalClaimCount;
```

For voucher-backed payments, `amountPerClaim` is interpreted as the fixed discount amount. `remainingRawAmount` is the remaining sponsor-funded voucher pool. `countLimitPerUser`, `totalAmountLimit`, `startTime`, `endTime`, `isActive`, `merkleRoot`, and `isCodeUsed` keep their existing meanings.

#### Interface

`PCECommunityToken` adds one public checkout entrypoint:

```solidity
function payWithVoucherAuthorization(
    address buyer,
    address merchant,
    string memory issuanceId,
    string memory code,
    bytes32[] calldata proof,
    uint256 paymentAmount,
    address relayer,
    uint256 validAfter,
    uint256 validBefore,
    bytes32 nonce,
    uint8 v,
    bytes32 r,
    bytes32 s
)
    external
    returns (uint256 userPaidAmount, uint256 discountAmount);
```

`buyer` is the wallet whose community-token balance pays the non-discounted part of the checkout. `merchant` is the recipient of both the buyer-paid amount and the voucher-funded amount. `paymentAmount` is the full merchant charge in display units. `discountAmount` is fixed to the issuance's `amountPerClaim`.

The function MUST reject zero addresses for `buyer`, `merchant`, and `relayer`. The function MUST require `msg.sender == relayer`. For self-submitted payments, the buyer sets `relayer = buyer` and submits the transaction directly.

#### EIP-712 Authorization

`PCECommunityToken` adds a voucher-payment authorization typehash:

```solidity
bytes32 public constant PAY_WITH_VOUCHER_AUTHORIZATION_TYPEHASH = keccak256(
    "PayWithVoucherAuthorization(address buyer,address merchant,string issuanceId,string code,uint256 paymentAmount,address relayer,uint256 validAfter,uint256 validBefore,bytes32 nonce)"
);
```

The signed digest MUST use the existing `DOMAIN_SEPARATOR()` of the community token and the existing authorization-state mapping used by EIP-3009 style functions:

```solidity
_digest(abi.encode(
    PAY_WITH_VOUCHER_AUTHORIZATION_TYPEHASH,
    buyer,
    merchant,
    keccak256(bytes(issuanceId)),
    keccak256(bytes(code)),
    paymentAmount,
    relayer,
    validAfter,
    validBefore,
    nonce
))
```

`payWithVoucherAuthorization` MUST:

1. Require `block.timestamp > validAfter`.
2. Require `block.timestamp < validBefore`.
3. Require `_authorizationStates[buyer][nonce] == false`.
4. Recover the signer from the digest and require it equals `buyer`.
5. Set `_authorizationStates[buyer][nonce] = true`.
6. Emit the existing `AuthorizationUsed(buyer, nonce)` event.

The authorization binds the claim code to a specific buyer, merchant, full payment amount, relayer, validity window, and nonce. A third party that sees the code in calldata can copy the transaction but cannot redirect the discount to another merchant or change the buyer-paid amount.

#### Payment Algorithm

Let:

```text
discountAmount = issuance.amountPerClaim
userPaidAmount = paymentAmount - discountAmount
```

The function MUST require `paymentAmount >= discountAmount`. PIP-20 keeps voucher discounts fixed-amount only and does not define partial voucher consumption. If the merchant charge is smaller than the voucher amount, the transaction MUST revert.

The algorithm is:

1. Call `updateFactorIfNeeded()`.
2. Validate the EIP-712 authorization described above.
3. Load the issuance and require it exists.
4. Require `issuance.isActive == true`.
5. Require `issuance.startTime == 0 || issuance.startTime < block.timestamp`.
6. Require `issuance.endTime == 0 || block.timestamp < issuance.endTime`.
7. Require `issuance.countLimitPerUser == 0 || claimCountPerUser[issuanceId][buyer] < issuance.countLimitPerUser`.
8. Require the Merkle proof verifies `keccak256(abi.encodePacked(code))` against `issuance.merkleRoot`.
9. Require `isCodeUsed[issuanceId][code] == false`.
10. Compute `discountAmount = issuance.amountPerClaim` and require `paymentAmount >= discountAmount`.
11. Convert `discountAmount` and `userPaidAmount` to raw units using `displayBalanceToRawBalance`.
12. Require `remainingRawAmount[issuanceId] >= rawDiscountAmount`.
13. If `issuance.totalAmountLimit > 0`, require `claimedDisplayAmount[issuanceId] + discountAmount <= issuance.totalAmountLimit`.
14. Compute the relayer fee: if `relayer == buyer`, `displayFee = 0`; otherwise `displayFee = getMetaTransactionFee()`.
15. Convert `displayFee` to raw units and require `balanceOf(buyer) >= rawUserPaidAmount + rawFee`.
16. Increment `claimCountPerUser[issuanceId][buyer]`.
17. Decrease `remainingRawAmount[issuanceId]` by `rawDiscountAmount`.
18. Increase `claimedRawAmount[issuanceId]` by `rawDiscountAmount`.
19. Increase `claimedDisplayAmount[issuanceId]` by `discountAmount`.
20. Increase `totalClaimCount[issuanceId]` by 1.
21. Set `isCodeUsed[issuanceId][code] = true`.
22. If `rawUserPaidAmount > 0`, transfer `rawUserPaidAmount` from `buyer` to `merchant` using the host's internal transfer hook.
23. Transfer `rawDiscountAmount` from `address(this)` to `merchant` using the host's internal transfer hook.
24. If `displayFee > 0`, collect the relayer fee from `buyer` via `_collectFeeAsPCE(buyer, relayer, displayFee)`.
25. Emit the events defined below.
26. Return `(userPaidAmount, discountAmount)`.

All state changes and transfers happen in one transaction. If any check or transfer fails, the buyer payment, voucher redemption, relayer fee collection, and usage markers all revert together.

#### Events

The existing `VoucherClaimed` event SHOULD continue to be emitted to preserve the current voucher indexing surface:

```solidity
event VoucherClaimed(
    string indexed issuanceId,
    address indexed claimer,
    string code,
    uint256 amount
);
```

For voucher-backed checkout, `claimer` is `buyer` and `amount` is `discountAmount`.

A new event is added for payment settlement:

```solidity
event VoucherPayment(
    string indexed issuanceId,
    address indexed buyer,
    address indexed merchant,
    string code,
    uint256 paymentAmount,
    uint256 userPaidAmount,
    uint256 discountAmount,
    address relayer
);
```

The existing `AuthorizationUsed(buyer, nonce)` event MUST be emitted when the signed authorization is consumed. If a relayer fee is collected, the existing `MetaTransactionFeeCollected` and `MetaTransactionFeeSwapped` events from PIP-13 MUST be emitted by `_collectFeeAsPCE`.

#### Library Placement

The heavy logic SHOULD live in `VoucherSystem.sol`, following the existing pattern used by `registerVoucherIssuance`, `claimVoucher`, and `claimVoucherWithAuthorization`. `PCECommunityToken` should expose the public wrapper and pass `_voucherStorage` and `_authorizationStates` into the library.

No new `transferWithAuthorizationWithVoucher`, `transferWithAuthorizationWithVoucherAndMeta`, or equivalent transfer-level function is introduced by this PIP.

### Rationale

**Why send the voucher amount directly to the merchant?**
A discount voucher is not a token grant. The sponsor funds should subsidize a specific checkout. Sending the voucher amount directly to the merchant makes the subsidy inseparable from the purchase and prevents a user from claiming the subsidy as freely spendable balance without paying a merchant.

**Why fixed amount only?**
The current voucher system already has `amountPerClaim`. Reusing it as the fixed discount amount keeps the extension minimal and avoids new percentage, cap, or minimum-spend rule storage. More complex discount rules can be introduced later as a separate proposal.

**Why require `paymentAmount >= amountPerClaim` instead of partially consuming a voucher?**
Partial consumption would require either new per-code residual accounting or a rule that burns the unused portion. Both change the voucher semantics. PIP-20 keeps existing one-code-one-claim semantics: a code consumes exactly `amountPerClaim`, and checkouts below that amount are rejected.

**Why require a voucher-payment authorization rather than a direct public payment function?**
Voucher codes are bearer credentials. With the existing Merkle proof model, anyone who sees a valid `code + proof` pair can use it. In a public mempool, a direct `payWithVoucher(issuanceId, code, proof, merchant, amount)` call would reveal the code before confirmation; a frontrunner could redeem the same code to their own merchant address. The buyer's EIP-712 authorization binds the code to the buyer, merchant, payment amount, relayer, validity window, and nonce, so a copied transaction cannot redirect the discount.

**Why include `relayer` in the signed data?**
If the relayer were not signed, a frontrunner could copy the calldata and take the relayer fee. Binding `relayer` and requiring `msg.sender == relayer` makes the signed authorization usable only by the intended transaction submitter. For self-submission, the buyer signs `relayer = buyer` and no relayer fee is charged.

**Why not extend `transferWithAuthorization`?**
The payment needs voucher-specific context: issuance ID, code, proof, merchant, full payment amount, and relayer. Adding this to the generic transfer layer would create a family of specialized transfer functions and typehashes. Keeping the signature in the voucher subsystem preserves `transferWithAuthorization` as a simple token-transfer primitive and prevents function-combination growth.

**Why reuse existing voucher storage?**
The current voucher storage already tracks all required budget and usage state: locked funds, per-user counts, total claimed amounts, code usage, activity status, and validity windows. PIP-20 only changes the settlement destination for a checkout-specific redemption.

### Backwards Compatibility

This proposal is additive:

- Existing `registerVoucherIssuance`, `claimVoucher`, `claimVoucherWithAuthorization`, `addVoucherFunds`, `withdrawVoucherFunds`, `terminateVoucherIssuance`, `getVoucherIssuanceInfo`, `getVoucherIssuanceIds`, and `canClaimVoucher` semantics remain unchanged.
- Existing `transfer`, `transferFrom`, `transferWithAuthorization`, `transferFromWithAuthorization`, and meta-transaction fee functions remain unchanged.
- No new voucher storage is required.
- The existing `_authorizationStates` mapping is reused for the new voucher-payment authorization nonce.
- Existing voucher issuances can be used for checkout discounts as long as their operational policy allows it. Applications that want to distinguish token-grant vouchers from payment-discount vouchers SHOULD use `ipfsCid` metadata and off-chain policy until a future proposal defines an on-chain issuance kind.

Indexers that already process `VoucherClaimed` continue to observe a voucher redemption. Payment-aware indexers can additionally subscribe to `VoucherPayment`.

### Test Cases

Assume a community token `CT`, voucher issuance `summer-2026`, `amountPerClaim = 200 CT`, `countLimitPerUser = 1`, and sufficient `remainingRawAmount` unless stated otherwise.

**Case 1: Standard discounted checkout**
- Merchant charge: `paymentAmount = 1,000 CT`
- Buyer authorization: buyer signs merchant, issuance ID, code, payment amount, relayer, validity window, and nonce
- Result: `userPaidAmount = 800 CT`, `discountAmount = 200 CT`
- Transfers: buyer pays `800 CT` to merchant; voucher locked funds pay `200 CT` to merchant
- Merchant receives `1,000 CT`
- `claimCountPerUser[issuanceId][buyer]` increments to 1
- `isCodeUsed[issuanceId][code]` becomes true

**Case 2: Voucher equals merchant charge**
- Merchant charge: `paymentAmount = 200 CT`
- Result: `userPaidAmount = 0`, `discountAmount = 200 CT`
- Buyer pays no checkout amount, but still signs the payment authorization
- Voucher locked funds pay `200 CT` to merchant

**Case 3: Merchant charge below voucher amount**
- Merchant charge: `paymentAmount = 150 CT`
- Voucher amount: `200 CT`
- Result: revert because `paymentAmount < amountPerClaim`
- Code remains unused and funds remain locked

**Case 4: Reused code**
- The same code was already used in Case 1
- A second call with the same code reverts with `Code already used`
- No payment or fee is collected

**Case 5: Wrong merchant**
- Buyer signs authorization for merchant A
- A third party submits the same code and signature with merchant B
- Result: signature recovery fails because `merchant` is part of the signed digest

**Case 6: Wrong relayer**
- Buyer signs authorization with `relayer = R1`
- `R2` submits the transaction
- Result: revert because `msg.sender != relayer`
- This prevents relayer-fee theft by calldata copying

**Case 7: Relayed checkout with fee**
- Buyer signs with `relayer = R1`
- `R1` submits the transaction
- `getMetaTransactionFee()` returns `0.002 CT`
- Result: buyer pays `800 CT` to merchant and `0.002 CT` equivalent fee via `_collectFeeAsPCE`; voucher locked funds pay `200 CT` to merchant; relayer receives PCE according to PIP-13

**Case 8: Insufficient buyer balance**
- Buyer has `799 CT` and relayer fee is zero
- Merchant charge is `1,000 CT`, voucher amount is `200 CT`
- Required buyer amount is `800 CT`
- Result: revert; voucher code remains unused

**Case 9: Insufficient voucher funds**
- `remainingRawAmount[issuanceId]` is less than the raw equivalent of `200 CT`
- Result: revert; buyer balance is unchanged and code remains unused

### Security Considerations

**Bearer-code leakage and front-running.** Voucher codes are bearer credentials under the current `keccak256(abi.encodePacked(code))` Merkle leaf model. PIP-20 therefore uses a buyer-signed voucher-payment authorization and does not rely on a bare public payment call. The signed digest binds the code to a buyer, merchant, payment amount, relayer, validity window, and nonce. A transaction copier can only submit the same authorized payment to the same merchant; they cannot redirect the voucher value.

**Relayer-fee theft.** The `relayer` address is signed and `msg.sender == relayer` is required. A third party cannot copy the transaction and collect the relayer fee.

**Atomicity.** Buyer payment, voucher subsidy, code usage, per-user count, total claimed counters, and relayer fee collection occur in one transaction. Any failure reverts all effects.

**Replay protection.** The existing `_authorizationStates[buyer][nonce]` mapping MUST be used. Once consumed, the same buyer nonce cannot authorize another voucher payment.

**Merchant spoofing.** The merchant address is part of the signed digest and is emitted in `VoucherPayment`. Wallets MUST display the merchant address or a verified merchant name before requesting the buyer signature.

**Code privacy.** Codes still appear in transaction calldata. Applications SHOULD submit voucher payments through the signed relayer flow and SHOULD avoid exposing unused codes to untrusted systems before the buyer authorization is created. Issuers that require stronger privacy or wallet binding SHOULD use off-chain distribution controls or propose a future leaf format that includes the buyer address.

**Rounding.** `discountAmount`, `userPaidAmount`, and relayer fee are display-unit values converted to raw units using the community token's existing factor conversion. Implementations MUST use the same conversion functions used by existing voucher claims and transfers. Rounding follows current token behavior.

**Contract balance accounting.** The voucher discount is paid from `address(this)`, which holds locked voucher funds. Implementations MUST check `remainingRawAmount[issuanceId] >= rawDiscountAmount` before transferring. The function MUST NOT pay discounts from unrelated user balances.

**Scope of authorization.** The new authorization is not a general-purpose transfer authorization. It only authorizes this voucher payment. Implementations MUST NOT expose a way to reuse the signature for ordinary transfers.

### Notes

- Related prototype: https://github.com/peacecoin-protocol/erc20-voucher/tree/feature/initial
- Related core implementation surface: `PCECommunityToken` voucher functions and `VoucherSystem.sol` in https://github.com/peacecoin-protocol/core
- Requires PIP-13 when the relayed path charges and auto-swaps meta-transaction fees to PCE.
- Out of scope: percentage discounts, maximum-discount caps, minimum purchase amounts, partial voucher consumption, on-chain merchant allowlists, wallet-bound Merkle leaves, and a separate `PaymentRouter` contract.
- Out of scope: changing existing token-grant voucher behavior. `claimVoucher` and `claimVoucherWithAuthorization` remain valid for token-grant campaigns.
- Future extension: an on-chain `issuanceKind` field could distinguish `TOKEN_GRANT` and `PAYMENT_DISCOUNT` campaigns. PIP-20 does not require it because the current `ipfsCid` metadata field can carry operational policy without a storage change.

### Copyright

Copyright and related rights waived via [CC0](https://creativecommons.org/publicdomain/zero/1.0/).
