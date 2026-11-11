---
pip: 17
title: Deposit Migration on Inter-community Swap
proposer: CHIBA Masahiro (@nihen)
status: Draft
type: Core
created: 2026-05-01
requires: []
replaces: []
---

## PIP-17: Deposit Migration on Inter-community Swap

### Abstract

This proposal fixes the per-community PCE reserve accounting of the direct community-to-community swap `PCECommunityToken.swapTokens(toToken, amount)`. Today the direct swap burns on the source and mints on the destination, but the per-community PCE reserves in `PCEToken.localTokens[*].depositedPCEToken` do not move; it also cannot complete, because the destination-side `mint` is called by the source community token while `PCECommunityToken.mint` accepts only `PCEToken`. This proposal routes the swap through a new `PCEToken.swapBetweenLocalTokens` function that moves the PCE reserve from the source community to the destination community and mints the destination token. After this change, the direct path works and is accounting-equivalent to the indirect path A → PCE → B. The direct swap does not consume the daily swap-to-PCE limits. To keep those limits effective, swaps to PCE from each community are also capped per day at `swapableToPCERate` of that community's reserve at the start of the UTC day. The per-community reserve model is retained.

### Motivation

`PCEToken` tracks PCE reserves per community in `localTokens[community].depositedPCEToken`. The swaps to and from PCE keep this accounting correct:

- `swapToLocalToken(B, amount)` (PCE → B) increases `localTokens[B].depositedPCEToken` by `amount`.
- `swapFromLocalToken(A, amount)` (A → PCE) decreases `localTokens[A].depositedPCEToken` by the PCE equivalent and consumes A's daily swap-to-PCE limits.
- `swapFeeFromLocalToken(...)` (meta-transaction fee) decreases the source community's reserve by the PCE equivalent of the fee.

The direct swap `PCECommunityToken.swapTokens(toToken, amount)` does not. It burns on the source and mints on the destination, but neither `localTokens[A].depositedPCEToken` nor `localTokens[B].depositedPCEToken` changes. (It also has a latent bug: it calls `to.mint(sender, amount)` from the source community token, while `PCECommunityToken.mint` accepts only `PCEToken` as the caller, so the call reverts. This proposal fixes it as a side effect of routing the swap through `PCEToken`.) If direct swaps moved value without moving reserves, the reserves would drift from the claims:

- **Source community A:** its holders have exited, but A's reserve still counts the PCE that backed them.
- **Destination community B:** its holders hold new claims, but B's reserve has no matching PCE.

The two consequences are:

1. **Swap-back fairness breaks.** When a B holder later calls `swapFromLocalToken(B, ...)`, the check `localTokens[B].depositedPCEToken >= pceAmount` reads a reserve that does not include the PCE that entered B through direct swaps. The swap-back can fail even though the PCE is in the protocol.
2. **Path equivalence breaks.** The direct path and the indirect path A → PCE → B are meant to be economically equivalent, but they would leave different `localTokens[*].depositedPCEToken` values.

### Specification

The key words "MUST", "MUST NOT", "SHOULD" and "MAY" are to be interpreted as described in RFC 2119.

#### Interface — PCEToken (new)

```solidity
/// Callable only by a registered community token (the source of the swap).
function swapBetweenLocalTokens(
    address toToken,
    address account,
    uint256 amountToSwap
) external returns (uint256 targetTokenAmount);

event TokensSwappedBetweenLocalTokens(
    address indexed account,
    address indexed fromToken,
    address indexed toToken,
    uint256 fromTokenAmount,
    uint256 pceTokenAmount,
    uint256 targetTokenAmount
);

/// Today's cap on PCE leaving `communityToken`'s reserve through swaps to PCE, and the PCE already counted.
function getDailyPCEOutflow(address communityToken) external view returns (uint256 limit, uint256 used);

/// PCE that can still leave `communityToken`'s reserve today through swaps to PCE.
function getRemainingDailyPCEOutflow(address communityToken) external view returns (uint256);

/// The cap and the remaining amount in `communityToken` display units (rounded down).
function getDailyPCEOutflowInToken(address communityToken) external view returns (uint256 limit, uint256 remaining);
```

#### Interface — PCECommunityToken

The public signature of `swapTokens(address toTokenAddress, uint256 amountToSwap)` is unchanged. Its body is moved into an internal `_swapTokens(address sender, address toTokenAddress, uint256 amountToSwap)`, which PIP-22's signed swap also uses.

#### Algorithm — `PCECommunityToken._swapTokens(sender, toToken, amount)`

1. Require `toToken != address(this)` (`"Cannot swap to same token"`).
2. Update the factors of this token and of `toToken`.
3. Require `balanceOf(sender) >= amount` (`"Insufficient balance"`), `isAllowOutgoExchange(toToken)` (`"Outgo exchange not allowed"`) and `toToken.isAllowIncomeExchange(address(this))` (`"Income exchange not allowed"`).
4. Burn `amount` (display units) from `sender`.
5. Call `PCEToken.swapBetweenLocalTokens(toToken, sender, amount)`.

The direct swap MUST NOT check or consume the daily swap-to-PCE limits (`getRemainingSwapableToPCEBalance`, `getRemainingSwapableToPCEBalanceForIndividual`).

#### Algorithm — `PCEToken.swapBetweenLocalTokens(toToken, account, amount)`

Let `fromToken = msg.sender`.

1. Require `localTokens[fromToken].isExists` (`"Caller must be a community token"`) and `localTokens[toToken].isExists` (`"Target token not found"`).
2. Update `PCEToken`'s factor and the factor of `toToken`.
3. Compute the PCE equivalent with the same formula as `swapFromLocalToken`:

   ```
   pceAmount = mulDiv(
       mulDiv(amount, INITIAL_FACTOR, localTokens[fromToken].exchangeRate),
       lastModifiedFactor,
       PCECommunityToken(fromToken).getCurrentFactor()
   )
   ```

   Require `pceAmount > 0` (`"Target token deposit low"`) and `localTokens[fromToken].depositedPCEToken >= pceAmount` (`"Insufficient deposited PCE token reserve"`).
4. Compute the destination amount with the same formula as `swapToLocalToken`:

   ```
   targetTokenAmount = mulDiv(
       mulDiv(pceAmount, localTokens[toToken].exchangeRate, INITIAL_FACTOR),
       PCECommunityToken(toToken).getCurrentFactor(),
       lastModifiedFactor
   )
   ```

   Require `targetTokenAmount > 0` (`"Invalid amount to swap"`).
5. Decrease `localTokens[fromToken].depositedPCEToken` and increase `localTokens[toToken].depositedPCEToken` by `pceAmount`.
6. Call `PCECommunityToken(toToken).mint(account, targetTokenAmount)`.
7. Emit `TokensSwappedBetweenLocalTokens(account, fromToken, toToken, amount, pceAmount, targetTokenAmount)` and return `targetTokenAmount`.

All arithmetic MUST use `Math.mulDiv`.

#### Daily PCE outflow cap

Because direct swaps do not consume the daily swap-to-PCE limits, a holder could otherwise move a community's reserve into a community they own and widen that community's supply-based limits (see Rationale). To prevent this, `PCEToken` caps swaps to PCE per community, in PCE units:

1. For each community token `c`, `PCEToken` records `midnightDeposited[c]`, `outflowToday[c]` and `day[c]` (the UTC day number, `timestamp / 1 days`).
2. Before every change to `localTokens[c].depositedPCEToken` — in `createToken`, `swapToLocalToken`, `swapFromLocalToken`, `swapBetweenLocalTokens` (both tokens), `swapFeeFromLocalToken` and `increaseTokenValue` — if `day[c]` is not today, set `midnightDeposited[c] = localTokens[c].depositedPCEToken`, `outflowToday[c] = 0` and `day[c]` to today. The first change of a day therefore records the reserve as it was at the start of the day.
3. In `swapFromLocalToken` (including the signed swap to PCE), require `outflowToday[c] + pceAmount <= midnightDeposited[c] * swapableToPCERate / 10000` (`"Exceeds daily PCE outflow limit"`) and add `pceAmount` to `outflowToday[c]`.
4. `PCECommunityToken.getTodaySwapableToPCEBalance()` and `getRemainingSwapableToPCEBalance()` return the smaller of their supply-based values and the cap and remaining amount from `PCEToken.getDailyPCEOutflowInToken(address(this))`. `swapFromLocalToken` already checks this value, so the cap usually reverts there with `"Exceeds daily swap limit"`; step 3 is the check in PCE units.

Relayer fees (`swapFeeFromLocalToken`) and direct swaps are not counted in `outflowToday`. The existing supply-based community and individual limits are unchanged; the effective community limit is the smaller of the two.

#### Storage

`PCEToken` appends one mapping, `mapping(address communityToken => DailyOutflow) _dailyOutflows`. `DailyOutflow` is packed into one slot: `uint112 midnightDeposited`, `uint112 outflowToday`, `uint32 day`. A reserve above `type(uint112).max` (about 5.2 × 10^15 PCE) is recorded as that maximum. No existing slot changes.

#### Versions

`PCEToken.version()` becomes `1.0.18` and `PCECommunityToken.version()` becomes `1.0.20` (released together with PIP-22).

### Rationale

**Why route the swap through `PCEToken`?** `localTokens` belongs to `PCEToken`, and `PCECommunityToken.mint` accepts only `PCEToken`. Calling a `PCEToken` function that only registered community tokens may call is the existing pattern (`swapFeeFromLocalToken`). It avoids widening the `mint` permission to every community token.

**Why reuse the `swapFromLocalToken` and `swapToLocalToken` formulas?** So that the direct swap gives the same destination amount and the same reserve changes as the indirect path. On a Polygon mainnet fork with the upgrade applied, swapping 3 tokens of one production community to another moved 2.6109 PCE of reserve and produced 3312.7299 destination tokens; swapping the same 2.6109 PCE through `swapToLocalToken` produced the same 3312.7299 tokens.

**Why do the daily swap-to-PCE limits not apply?** The limits throttle swaps to PCE. A direct swap does not pay out PCE: the reserve moves from A to B and stays in the protocol. When a B holder later swaps to PCE, B's limits apply. Applying A's limits to the direct swap would not reduce the PCE that leaves the protocol.

**Why cap swaps to PCE by the reserve at the start of the day?** The daily limits exist to slow the rate at which PCE leaves the protocol; protecting a community's holders is the job of the exchange allow lists. The existing community limit is a share of the community token's supply, which the community owner can raise without adding PCE: with ARIGATO CREATION settings at their maximum, repeated transfers can multiply the supply about 7.5 times a day, and `splitToken` multiplies the display supply while today's used amount stays in display units. That is harmless while a community holds only its owner's own PCE. With direct swaps outside the limits, however, anyone can create a community C, move a holder's share of another community's reserve into C on the same day, inflate C, and take that PCE out in a few days instead of at 3% a day. Capping by the reserve closes this: inflating the supply does not raise the cap, splitting the PCE among several communities does not raise the total, and PCE that enters a community today does not count until the next day. On the production communities at the time of writing, the reserve-based cap is equal to or larger than the supply-based limit for every community, so the effective limits do not change.

**Why not apply the source community's limits to the direct swap instead?** That also closes the path, but it treats a swap that pays out no PCE as an exit to PCE. The cap keeps direct swaps free and limits only what actually leaves the protocol.

**Why revert on a zero PCE equivalent instead of skipping?** A swap whose PCE equivalent rounds to zero would burn the user's tokens and either mint nothing or mint without moving reserves. Reverting is the same behavior as `swapFromLocalToken`.

**Why move the full PCE equivalent instead of a proportional share of the source reserve?** The destination holders receive full claims. Moving less would hide the source's under-collateralization in the destination. Reverting when the source reserve is short matches `swapFromLocalToken`.

**Why keep per-community reserves?** A single global pool was considered and rejected. Per-community reserves keep one community's reserve from paying for another community's exits, and keep each community's reserve number meaningful.

### Backwards Compatibility

- `swapTokens` keeps its signature. It was not usable before (the destination mint reverted), so no integration can depend on its old behavior.
- `PCEToken` gains four external functions and one event.
- `PCEToken` appends one mapping. No existing slot changes.
- Swaps to PCE can now also revert because of the reserve-based cap. `getTodaySwapableToPCEBalance()` and `getRemainingSwapableToPCEBalance()` include the cap, so clients that size swaps with them are not affected.
- For each community, the cap starts from the reserve at the first reserve change after the upgrade.
- The direct swap reverts when the source reserve does not cover the swap. It does not check or consume the daily swap-to-PCE limits.
- Reserve drift from before the upgrade is not corrected. The on-chain state at the upgrade is the new baseline.

### Test Cases

Unless noted, exchange rates and factors are `1e18` and the daily limits are high enough not to bind. Cases 1–13 are covered by `test/SwapTokens.t.sol` and case 14 by `test/BytesSignatureAuthorization.t.sol` in `peacecoin-protocol/core` at `v20`. The amounts in case 1 are illustrative.

1. **Standard swap.** `localTokens[A].depositedPCEToken = 100`, `localTokens[B].depositedPCEToken = 50`, and the user holds 30 A. The user calls `A.swapTokens(B, 30)`. Result: the user's A balance decreases by 30, B mints 30 to the user, `localTokens[A].depositedPCEToken = 70`, `localTokens[B].depositedPCEToken = 80`, and `TokensSwappedBetweenLocalTokens(user, A, B, 30, 30, 30)` is emitted.
2. **Daily limits are not consumed.** After case 1, `A.getRemainingSwapableToPCEBalance()` and the user's individual remaining balance on A are unchanged. A direct swap larger than either remaining balance succeeds.
3. **Allow lists.** A does not allow outgoing exchange to B, or B does not allow incoming exchange from A. Result: revert `"Outgo exchange not allowed"` or `"Income exchange not allowed"`.
4. **Same token.** `A.swapTokens(A, 30)`. Result: revert `"Cannot swap to same token"`.
5. **Unregistered target.** B is a community-token contract that is not registered in `PCEToken`. Result: revert `"Target token not found"`.
6. **Caller check.** An EOA or an unregistered contract calls `PCEToken.swapBetweenLocalTokens`. Result: revert `"Caller must be a community token"`.
7. **Outflow is counted.** `swapableToPCERate = 30%` and A's reserve at the start of the day is 1000. `getRemainingDailyPCEOutflow(A) = 300`. After a swap of 10 A to PCE, it is 290.
8. **Cap by reserve.** A's reserve drops from 1000 to 100 without burning A (relayer fees). On the next day the supply-based limit is still about 300 A, but `A.getTodaySwapableToPCEBalance() = 30` and `A.getRemainingSwapableToPCEBalance() = 30`. A swap of 30 A to PCE succeeds; one more unit reverts with `"Exceeds daily swap limit"`. If the community token reported a larger remaining balance, `PCEToken` would still revert with `"Exceeds daily PCE outflow limit"`.
9. **Inflow does not count today.** A user swaps 50 A to B, or anyone calls `swapToLocalToken` or `increaseTokenValue` on A. The remaining outflow of A and of B is unchanged today. On the next day, the cap is 30% of the new reserve. PCE that enters at 23:59:59 counts from 00:00:00.
10. **New community.** On the day a community is created, its cap is 0. On the next day it is 30% of its reserve.
11. **Split does not reopen the cap.** After a swap to PCE, the owner calls `splitToken` to double A's display supply. `getRemainingDailyPCEOutflow(A)` is unchanged.
12. **Self-made community.** A holder creates community C and swaps 500 A to C. On the creation day C's cap is 0. On the next day, after splitting C twice, the PCE the holder can take out of C that day is at most 30% of C's reserve at the start of the day.
13. **Fees.** After A's cap is used up, a relayer fee from A still succeeds and is not counted.
14. **Signed direct swap.** `swapTokensWithAuthorization` does not change the individual remaining balance on A.
15. **Reserve invariant.** For any sequence of direct swaps, the sum of `localTokens[c].depositedPCEToken` over all registered communities is unchanged. (This follows from step 5 of the algorithm and is not a separate test.)

### Security Considerations

- **Caller authorization.** `swapBetweenLocalTokens` MUST accept only registered community tokens as `msg.sender`, and MUST require the destination to be registered. An unregistered contract cannot move reserves.
- **Burn before mint.** The source burns before calling `PCEToken`. If any later step reverts, the whole transaction reverts, including the burn.
- **External calls.** The function calls only registered community tokens (`updateFactorIfNeeded`, `getCurrentFactor`, `mint`). The registry is the trust boundary, as it is for the existing swap functions.
- **Daily limits.** A direct swap pays out no PCE, so the swap-to-PCE limits do not apply. A holder who goes A → B → PCE consumes B's limits on the last step. Because the community owner can inflate the supply that the supply-based limit uses, the reserve-based cap MUST bound swaps to PCE, and the snapshot MUST be taken before every change to the reserve. Missing one call would let PCE that entered today count toward today's cap.
- **Relayer fees.** Fees stay outside the limits and the cap, as before. They are bounded by the fee settings and by `maxFee`.
- **Ordering.** Direct swaps now compete with `swapFromLocalToken` for the source reserve, first come first served. This is the same property the indirect path already has.

### Notes

- **Implementation:** `peacecoin-protocol/core` PRs #50 and #53, tag `v20`. PIP-22 builds on this PIP for its signed inter-community swap.
- **Changes from the earlier draft (2026-05-01):** the new function is `swapBetweenLocalTokens(toToken, account, amount)` instead of `executeInterCommunitySwap(fromToken, toToken, recipient, fromDisplayAmount, toDisplayAmount)`; `PCEToken` computes the destination amount itself instead of receiving it; the event is `TokensSwappedBetweenLocalTokens`; a zero PCE equivalent reverts; swaps to PCE are also capped by the reserve at the start of the day. As in the earlier draft, the daily swap-to-PCE limits do not apply to the direct swap.
- **Out of scope:**
  - Correcting reserve drift from before the upgrade.
  - The other known sources of difference between reserves and claims (ARIGATO CREATION mints and decay). They are properties of the existing reserve model.
  - A global reserve pool.
