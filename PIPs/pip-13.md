---
pip: 13
title: Meta-Transaction Fee Auto-Swap to PCE
proposer: CHIBA Masahiro (@nihen)
status: Draft
type: Core
created: 2026-03-13
requires: []
replaces: []
---

## PIP-13: Meta-Transaction Fee Auto-Swap to PCE

### Abstract

This proposal changes meta-transaction fee settlement from direct community token transfer to automatic PCE conversion. When a relayer executes a meta-transaction, the fee collected from the user in community tokens is burned and the equivalent amount of PCE is transferred to the relayer. This swap is exempt from daily swap limits.

### Motivation

In the current design, meta-transaction fees are collected in the community token being transacted and transferred directly to the relayer (`_msgSender()`). This creates two problems:

1. **Relayer token fragmentation**: Relayers accumulate many types of community tokens across communities. These tokens have limited immediate utility — relayers need PCE (or native tokens) to cover gas costs, not a portfolio of community tokens.

2. **Manual swap burden with rate limits**: To convert accumulated community tokens to PCE, relayers must call `swapFromLocalToken` for each token type, subject to daily global and individual swap limits. During high-activity periods, relayers may be unable to swap their earned fees due to exhausted limits.

This proposal solves both problems by automatically converting fees to PCE at the point of collection, exempt from swap limits.

### Specification

#### Interface

**PCEToken — new function:**

```solidity
/// @notice Swaps community token fee to PCE and transfers to relayer.
///         Only callable by the community token contract itself.
///         Exempt from daily swap limits.
/// @param fromToken The community token contract address (must equal msg.sender)
/// @param relayer The relayer address to receive PCE
/// @param communityTokenDisplayAmount The fee amount in community token display units
/// @return pceAmount The amount of PCE transferred to the relayer
function swapFeeFromLocalToken(
    address fromToken,
    address relayer,
    uint256 communityTokenDisplayAmount
) external returns (uint256 pceAmount);
```

**PCECommunityToken — new internal function:**

```solidity
/// @notice Burns community token fee from the payer and sends equivalent PCE to the relayer.
/// @param from The address paying the fee (token holder)
/// @param relayer The relayer address to receive PCE
/// @param displayFee The fee amount in community token display units
function _collectFeeAsPCE(
    address from,
    address relayer,
    uint256 displayFee
) internal returns (uint256 pceAmount);
```

**PCECommunityToken — modified functions:**

The following functions replace their `super._transfer(from, _msgSender(), rawFee)` call with `_collectFeeAsPCE(from, _msgSender(), displayFee)`:

- `transferWithAuthorization`
- `transferFromWithAuthorization`
- `setInfinityApproveFlagWithAuthorization`
- `claimVoucherWithAuthorization`

No changes are made to `getMetaTransactionFee()` or `getMetaTransactionFeeWithBaseFee()` — the fee is still denominated and displayed in community token units.

#### Storage

No new storage variables are required. The existing swap rate parameters (`exchangeRate`, `getCurrentFactor()`, `lastModifiedFactor`) are reused for the fee conversion calculation.

#### Events

**PCECommunityToken — new event (emitted alongside the existing `MetaTransactionFeeCollected`):**

```solidity
event MetaTransactionFeeSwapped(
    address indexed from,
    address indexed relayer,
    uint256 communityTokenFee,
    uint256 pceFee
);
```

**PCEToken — new event:**

```solidity
event FeeSwappedFromLocalToken(
    address indexed fromToken,
    address indexed relayer,
    uint256 communityTokenAmount,
    uint256 pceAmount
);
```

The existing `MetaTransactionFeeCollected(from, to, displayFee, rawFee)` event continues to be emitted on every fee collection. Its semantics — "the payer `from` was charged `displayFee` / `rawFee` community tokens for a meta-transaction relayed by `to`" — are unchanged: only the relayer's settlement asset has changed (PCE instead of community tokens), and that detail is surfaced via the new `MetaTransactionFeeSwapped` event.

#### Fee Swap Algorithm

When `_collectFeeAsPCE(from, relayer, displayFee)` is called:

1. Convert `displayFee` to raw amount: `rawFee = displayBalanceToRawBalance(displayFee)`
2. Burn community tokens from `from`: `_burn(from, rawFee)`
3. Call `PCEToken(pceAddress).swapFeeFromLocalToken(address(this), relayer, displayFee)`
4. Emit `MetaTransactionFeeCollected(from, relayer, displayFee, rawFee)` (preserved for backward compatibility)
5. Emit `MetaTransactionFeeSwapped(from, relayer, displayFee, pceAmount)`

When `PCEToken.swapFeeFromLocalToken(fromToken, relayer, communityTokenDisplayAmount)` is called:

1. Verify `msg.sender == fromToken` and `localTokens[fromToken].isExists`
2. Calculate PCE amount using the same formula as `swapFromLocalToken`:
   ```
   pceAmount = communityTokenDisplayAmount
               × INITIAL_FACTOR / exchangeRate
               × lastModifiedFactor / target.getCurrentFactor()
   ```
3. Transfer PCE to relayer: `_transfer(address(this), relayer, pceAmount)`
4. **Do NOT** call `target.recordSwapToPCE()` — fee swaps are exempt from daily limits
5. Emit `FeeSwappedFromLocalToken(fromToken, relayer, communityTokenDisplayAmount, pceAmount)`

#### Special Case: Voucher Claims

In `claimVoucherWithAuthorization`, the fee source is `address(this)` (the contract itself), not an end user. The same `_collectFeeAsPCE(address(this), relayer, displayFee)` call applies — community tokens held by the contract are burned and PCE is sent to the relayer.

### Rationale

**Why auto-swap instead of letting relayers swap manually?**
Relayers operate infrastructure that serves all communities. Requiring them to manage and swap dozens of community token types creates unnecessary operational overhead. Auto-swap reduces this to zero by giving relayers exactly what they need: PCE.

**Why exempt from daily swap limits?**
Daily swap limits exist to prevent rapid economic destabilization from large speculative swaps. Meta-transaction fees are fundamentally different: they are small, predictable amounts driven by gas costs. Subjecting them to swap limits would cause meta-transaction failures when limits are exhausted, degrading user experience for all community members. The fee amounts are too small to pose any destabilization risk.

**Why burn-and-transfer instead of transfer-and-swap?**
Burning community tokens in the fee payer's context and transferring PCE from the PCEToken contract mirrors the existing `swapFromLocalToken` mechanic. This maintains consistency in how community token supply and PCE reserves interact.

**Why add `MetaTransactionFeeSwapped` instead of replacing `MetaTransactionFeeCollected`?**
The fee-collection semantics that `MetaTransactionFeeCollected` describes — *the payer was charged `displayFee` / `rawFee` community tokens for a meta-transaction relayed by `to`* — are unchanged by this PIP. Existing indexers that consume the event to track fee burden per payer remain correct. The new `MetaTransactionFeeSwapped` event additionally surfaces the PCE amount the relayer received, which is information the original event cannot express. Emitting both events keeps existing indexers working while letting new consumers observe the PCE settlement explicitly.

### Backwards Compatibility

**Breaking changes:**

- **Relayer fee denomination**: Relayers now receive PCE instead of community tokens. Relayer software MUST be updated to expect PCE balance increases rather than community token balance increases.

**Non-breaking:**

- `MetaTransactionFeeCollected(from, to, displayFee, rawFee)` is still emitted on every meta-transaction fee collection, with the same signature and semantics (payer-side fee burden). Off-chain indexers consuming this event continue to work without changes. Indexers that additionally need the PCE amount paid to the relayer can subscribe to the new `MetaTransactionFeeSwapped` event.
- `getMetaTransactionFee()` and `getMetaTransactionFeeWithBaseFee()` return values are unchanged (community token display units). Client-side fee estimation requires no changes.
- User experience is unchanged — users still pay the same fee amount in community tokens.
- No storage layout changes.

### Test Cases

All examples use `INITIAL_FACTOR = 10^18`.

**Case 1: Standard fee swap**
- Community token: exchangeRate = `2e18`, getCurrentFactor = `1e18`
- PCEToken: lastModifiedFactor = `1e18`
- Meta-transaction fee: `0.002` community tokens (displayFee)
- PCE calculation: `0.002 × 1e18 / 2e18 × 1e18 / 1e18 = 0.001` PCE
- Result: `0.002` community tokens burned from user, `0.001` PCE transferred to relayer
- Daily swap counters: unchanged

**Case 2: Fee swap with demurrage factors**
- Community token: exchangeRate = `2e18`, getCurrentFactor = `0.99e18` (1% demurrage applied)
- PCEToken: lastModifiedFactor = `0.98e18` (2% demurrage applied)
- Meta-transaction fee: `0.002` community tokens (displayFee)
- PCE calculation: `0.002 × 1e18 / 2e18 × 0.98e18 / 0.99e18 = 0.000990909...` PCE
- Result: community tokens burned, PCE transferred with factor adjustment

**Case 3: Fee swap does not affect daily limits**
- Daily global swap limit: `1000` community tokens, already used: `1000` (exhausted)
- Daily individual swap limit: `100` community tokens, already used: `100` (exhausted)
- Meta-transaction fee: `0.002` community tokens
- Result: Fee swap succeeds. Regular `swapFromLocalToken` would revert with "Exceeds daily swap limit", but fee swap bypasses limit checks entirely.

**Case 4: Voucher claim fee swap**
- Community token contract holds `10` community tokens (voucher balance)
- Claimer calls `claimVoucherWithAuthorization`
- Fee: `0.002` community tokens
- Result: `0.002` community tokens burned from contract balance, equivalent PCE sent to relayer. Claimer receives remaining voucher amount minus fee.

**Case 5: Insufficient PCE reserves**
- PCEToken contract holds `0` PCE (all reserves depleted)
- Meta-transaction fee swap attempts to transfer PCE to relayer
- Result: Transaction reverts. The meta-transaction cannot be executed.

### Security Considerations

**Swap limit bypass is restricted to fee amounts**: The `swapFeeFromLocalToken` function can only be called by registered community token contracts (`msg.sender == fromToken`). The community token contracts only call this function during fee collection in `*WithAuthorization` functions, where the fee amount is deterministically calculated from gas parameters set by the contract owner. An attacker cannot inflate the fee amount to exploit the limit bypass.

**PCE reserve depletion**: Fee swaps draw from the same PCE reserves as regular swaps. In extreme scenarios with very high meta-transaction volume across many communities, cumulative fee swaps could deplete PCE reserves faster than expected. However, individual fee amounts are negligible compared to regular swap volumes (typically < 0.01 PCE per transaction). Implementations SHOULD monitor PCE reserve levels.

**Reentrancy**: `_collectFeeAsPCE` burns tokens (state change) before making an external call to `PCEToken.swapFeeFromLocalToken`. The PCEToken function performs a `_transfer` (state change). This follows the checks-effects-interactions pattern within each contract. Implementations SHOULD use a reentrancy guard on `swapFeeFromLocalToken` as defense-in-depth.

**Rounding**: The fee undergoes two conversions: PCE → community token (in `getMetaTransactionFee`) → PCE (in `swapFeeFromLocalToken`). Due to integer division, the relayer may receive slightly less PCE than the theoretical gas cost. The rounding error is bounded by 1 wei per conversion step and is negligible in practice.

**Atomicity**: The burn and PCE transfer occur in the same transaction. If the PCE transfer fails (e.g., insufficient reserves), the entire meta-transaction reverts. This prevents a state where community tokens are burned but no PCE is received.

### Notes

- **Scope of impact**: Requires upgrades to both PCEToken and PCECommunityToken contracts
- **Interaction with PIP-12**: If PIP-12 (Community Treasury Wallet and Capital Operations) is implemented, fee swaps reduce both the PCEToken contract's PCE balance and the community's `depositedPCEToken`. This is consistent with how regular `swapFromLocalToken` operates — both functions decrease `depositedPCEToken` when PCE leaves the contract.
- **Out of scope**: Relayer fee margin configuration (e.g., markup for profit) is not addressed. Native token conversion (PCE → POL) for actual gas payment remains the relayer's responsibility.
- **Dependencies**: None (builds on existing meta-transaction and swap infrastructure)
