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

This proposal fixes a long-standing accounting drift in inter-community community-token swaps. Today, when a user calls `PCECommunityToken.swapTokens(toToken, amount)` to swap directly from community A to community B, raw supply is intended to move (burn on A, mint on B) but the per-community PCE reserve accounting in `PCEToken.localTokens[*].depositedPCEToken` does **not** move — and in fact, the destination-side `mint` call cannot succeed at all under the current code because `PCECommunityToken.mint` is gated to the PCEToken address. This proposal introduces a single PCEToken function, `executeInterCommunitySwap`, that the source community token calls after the source-side burn. PCEToken performs the deposit migration (moving the PCE-equivalent of the swapped amount from the source community's accounting to the destination community's accounting) and performs the destination-side mint (which it is authorized to do). After this change, the direct A→B path is functional end-to-end and becomes accounting-equivalent to the existing indirect A→PCE→B path. The per-community reserve model is retained.

### Motivation

`PCEToken` tracks PCE reserves on a per-community basis via `localTokens[community].depositedPCEToken`. Three out of four reserve-affecting flows already maintain this accounting correctly:

- `swapToLocalToken(B, amount)` (PCE → B): increments `localTokens[B].depositedPCEToken` by `amount`.
- `swapFromLocalToken(A, amount)` (A → PCE): decrements `localTokens[A].depositedPCEToken` by the PCE-equivalent.
- `swapFeeFromLocalToken(...)` (meta-tx fee path): decrements the source community's deposit by the PCE-equivalent fee.

The fourth — direct community-to-community swap via `PCECommunityToken.swapTokens(toToken, amount)` — does not. It is intended to burn raw supply on the source and mint on the destination, but **never touches** `localTokens[fromToken].depositedPCEToken` or `localTokens[toToken].depositedPCEToken`. (It also has a latent bug whereby the destination-side `mint(sender, targetTokenAmount)` call cannot succeed, because `PCECommunityToken.mint` is gated to the PCEToken address; the mint call is made from the source community token rather than from PCEToken, so the auth check rejects it. PIP-17 closes this by routing the mint through PCEToken alongside the deposit migration.) Over time and across many inter-community swaps, this produces a deposit-vs-claim drift between communities:

- Source community A: holders have already exited (raw supply burned), but PCE reserve accounting still attributes that PCE backing to A.
- Destination community B: holders now hold the claim (raw supply minted), but PCE reserve accounting attributes no additional backing to B.

The two consequences are:

1. **Per-community swap-back fairness breaks.** When a B holder later calls `swapFromLocalToken(B, ...)` to exit to PCE, the success/failure check (`localTokens[B].depositedPCEToken >= pcetokenAmount`) consults a deposit number that does not reflect the PCE that actually entered B (via inter-community swap). B can fail swap-back even though, in PCE-denominated terms across the protocol, B-attributable deposits should have been there.
2. **Path equivalence breaks.** A user who needs to move from A's tokens to B's tokens has two paths today: indirect (`swapFromLocalToken(A) → swapToLocalToken(B)`) and direct (`swapTokens(B)`). The two paths produce **different** post-state accounting in `localTokens[*].depositedPCEToken`, even though they are economically intended to be equivalent. This is a footgun for governance, for off-chain accounting, and for any future logic that reads per-community deposit values.

Note: this proposal scope is narrow. It does **not** restructure the per-community reserve model into a global pool (a separate alternative considered and explicitly rejected in favor of preserving per-community accounting). It does not address the orthogonal drifts described in the same design discussion: ARIGATO CREATION mints inflate community-token claims without inflating deposits, and PCE BASE Token decay erodes claims without changing deposits. Those remain per-community properties of the existing reserve model and are out of scope here.

### Specification

#### Interface

A new function is added to **PCEToken**:

```solidity
function executeInterCommunitySwap(
    address fromToken,
    address toToken,
    address recipient,
    uint256 fromDisplayAmount,
    uint256 toDisplayAmount
) external returns (uint256 movedPceAmount);
```

**Authorization:** the caller MUST be the source community token: `require(msg.sender == fromToken, "Caller must be the from-side community token")`. Both `fromToken` and `toToken` MUST be registered (`localTokens[*].isExists`). `fromToken != toToken` MUST hold.

**Behavior:** the function (a) computes the PCE-equivalent of `fromDisplayAmount` using the source community's exchange rate and current factor and migrates that amount of deposit from `localTokens[fromToken]` to `localTokens[toToken]`, then (b) calls `PCECommunityToken(toToken).mint(recipient, toDisplayAmount)`. The destination-side `mint` is performed by PCEToken (rather than by the source community token) because `PCECommunityToken.mint` is gated to the PCEToken address. If the source's recorded deposit is insufficient to cover the PCE-equivalent, the call MUST revert.

**PCECommunityToken.swapTokens — modified behavior:** the source community token still performs the source-side burn and the swap-amount calculation. The destination-side mint is now delegated to PCEToken via `pceToken.executeInterCommunitySwap(address(this), toTokenAddress, sender, amountToSwap, targetTokenAmount)`. The public ABI of `swapTokens` is unchanged.

#### Storage

No new storage. Existing slots used:

- `PCEToken.localTokens[fromToken].depositedPCEToken` (decremented)
- `PCEToken.localTokens[toToken].depositedPCEToken` (incremented)
- `PCEToken.localTokens[fromToken].exchangeRate` (read)
- `PCEToken.lastModifiedFactor` (read; updated by the `updateFactorIfNeeded()` call performed at the start of the function)
- The destination community's `_balances` / `_totalSupply` (mutated by the embedded `mint` call)

#### Events

```solidity
event DepositTransferredOnInterCommunitySwap(
    address indexed fromToken,
    address indexed toToken,
    uint256 fromDisplayAmount,
    uint256 movedPceAmount
);
```

Emitted by PCEToken on every successful call to `executeInterCommunitySwap`, including the zero-`movedPceAmount` no-op short-circuit case described below.

#### Algorithm

The PCE-equivalent of `fromDisplayAmount` (display amount being swapped out of `fromToken`) is computed using the same formula already used by `swapFromLocalToken` (`PCEToken.sol:288-292`) and `swapFeeFromLocalToken` (`PCEToken.sol:333-337`):

```
movedPceAmount = mulDiv(
    mulDiv(fromDisplayAmount, INITIAL_FACTOR, localTokens[fromToken].exchangeRate),
    lastModifiedFactor,
    PCECommunityToken(fromToken).getCurrentFactor()
)
```

This is the same PCE quantity that would result from the indirect path `swapFromLocalToken(fromToken, fromDisplayAmount)`. Therefore, after this PIP, the direct A→B path produces the same `localTokens[A].depositedPCEToken` decrement and `localTokens[B].depositedPCEToken` increment as the indirect A→PCE→B path.

**Steps in `executeInterCommunitySwap`:**

1. Verify `msg.sender == fromToken` (MUST).
2. Verify `localTokens[fromToken].isExists` and `localTokens[toToken].isExists` (MUST).
3. Verify `fromToken != toToken` (MUST).
4. Call `updateFactorIfNeeded()` so `lastModifiedFactor` reflects the current epoch.
5. Compute `movedPceAmount` using the formula above.
6. If `movedPceAmount > 0`: verify `localTokens[fromToken].depositedPCEToken >= movedPceAmount`, otherwise revert with `"Insufficient deposited PCE token reserve at source community"` (MUST). Then decrement `localTokens[fromToken].depositedPCEToken` and increment `localTokens[toToken].depositedPCEToken` by `movedPceAmount`. (When `movedPceAmount == 0`, no deposit accounting changes are made; this short-circuit mirrors `swapFeeFromLocalToken`'s zero-amount handling and prevents bricking on dust-sized swaps.)
7. Call `PCECommunityToken(toToken).mint(recipient, toDisplayAmount)`. The mint succeeds because PCEToken is the authorized caller for `PCECommunityToken.mint`.
8. Emit `DepositTransferredOnInterCommunitySwap(fromToken, toToken, fromDisplayAmount, movedPceAmount)`.
9. Return `movedPceAmount`.

**Modified `PCECommunityToken.swapTokens`:**

```solidity
function swapTokens(address toTokenAddress, uint256 amountToSwap) public {
    address sender = _msgSender();
    updateFactorIfNeeded();
    PCEToken pceToken = PCEToken(pceAddress);
    pceToken.updateFactorIfNeeded();
    PCECommunityToken to = PCECommunityToken(toTokenAddress);
    to.updateFactorIfNeeded();

    require(balanceOf(sender) >= amountToSwap, "Insufficient balance");
    require(isAllowOutgoExchange(toTokenAddress), "Outgo exchange not allowed");
    require(to.isAllowIncomeExchange(address(this)), "Income exchange not allowed");

    uint256 targetTokenAmount = TokenValueOps.computeSwapAmount(
        pceAddress, address(this), toTokenAddress, amountToSwap, getCurrentFactor(), to.getCurrentFactor()
    );
    super._burn(sender, displayBalanceToRawBalance(amountToSwap));

    // PIP-17: PCEToken handles deposit migration AND the destination-side mint
    // (which is gated to PCEToken). This both fixes the latent inability of
    // pre-PIP-17 swapTokens to mint on the destination community, and ensures
    // per-community deposit accounting tracks the actual claim transferred.
    pceToken.executeInterCommunitySwap(
        address(this), toTokenAddress, sender, amountToSwap, targetTokenAmount
    );
}
```

The previous `to.mint(sender, targetTokenAmount)` call is removed and replaced by the `executeInterCommunitySwap` call which performs both the mint and the deposit migration atomically.

#### Authority

`executeInterCommunitySwap` follows the same pattern as `swapFeeFromLocalToken`: it is callable only by the registered community token instance that the call concerns (`msg.sender == fromToken`). No governance role and no community-owner role are involved. The function cannot be invoked by EOAs, generic multisigs, or any contract other than the source community token.

There is no per-community opt-out: any direct inter-community swap initiated through `PCECommunityToken.swapTokens` performs the deposit migration. This is intentional — silent opt-outs would re-introduce the path-divergence between direct and indirect swaps that this PIP is designed to remove.

### Rationale

**Why a community-pool model rather than a global pool?**
A separate proposal considered consolidating per-community deposit accounting into a single global pool, removing the per-community check from `swapFromLocalToken` and routing all swap-back validation against `balanceOf(address(this))`. After review, the protocol elected to retain per-community accounting because it preserves three properties that the global-pool design would have lost: (a) inter-community cross-subsidy is bounded — community A's deposit cannot silently pay for community B's swap-back; (b) governance reasoning at the per-community level remains tractable — a community's deposit number means what it appears to mean; (c) per-community swap-back failure surfaces under-collateralization at the right unit of attribution. PIP-17 is the minimal change that fixes the specific drift caused by direct A→B swaps **without** discarding the per-community model.

**Why a new function on PCEToken rather than direct storage manipulation from PCECommunityToken?**
`localTokens` is PCEToken's internal accounting state. The existing pattern for community tokens that need PCEToken to mutate this state is to call a PCEToken function gated to `msg.sender == fromToken` (see `swapFeeFromLocalToken`, `PCEToken.sol:321`). PIP-17 follows the same pattern. Direct cross-contract storage writes are not a feature of the existing architecture and would require either making `localTokens` writable by community tokens or duplicating the registry — both worse than reusing the established pattern.

**Why move the full PCE-equivalent of the swap rather than a proportional share of the source deposit?**
A proportional design (`movedPceAmount = sourceDeposit * rawShareLeaving / sourceTotalRawSupply`) was considered. It has the property that under-collateralized source communities can still complete inter-community swaps without revert. It was rejected because (a) it would silently understate the destination community's required reserve attribution — the destination acquires holders with full PCE-denominated claims, and partial deposit migration would push the under-collateralization onto the destination invisibly; (b) it would diverge from `swapFromLocalToken`'s strict "deposit must cover claim" semantics, breaking the path equivalence that PIP-17 is trying to establish; (c) revert on under-collateralized source is the correct surfacing behavior — it matches what would happen if the user used the indirect path `swapFromLocalToken → swapToLocalToken`.

**Why bundle the destination-side mint into PCEToken's new function rather than keep it in PCECommunityToken?**
`PCECommunityToken.mint` is gated to `_msgSender() == pceAddress`, so it can only be invoked from PCEToken. Pre-PIP-17 code attempted `to.mint(sender, targetTokenAmount)` directly from the source community token, which would fail the auth check at runtime — making `swapTokens` non-functional. PIP-17 routes the mint through `executeInterCommunitySwap` so PCEToken (the authorized caller) performs it. This both fixes the latent bug and avoids widening `PCECommunityToken.mint`'s authorization to all registered community tokens, which would expand the trust surface unnecessarily.

**Why call `executeInterCommunitySwap` after the source-side burn rather than before?**
Order does not affect the computed `movedPceAmount` (the formula depends on `fromDisplayAmount` and the source community's `exchangeRate` / `getCurrentFactor()`, none of which are changed by burn). Calling after burn keeps the `swapTokens` body's source-side operations grouped, with the destination-side operations (deposit migration + mint) delegated together to PCEToken. If the call reverts (insufficient source deposit, unregistered destination, etc.), the entire transaction reverts including the burn, leaving the user's source-token balance unchanged. This is the desired atomicity.

**Why no new daily-limit accounting on inter-community swap?**
Inter-community swaps currently do not consume `swappedToPCEToday` (the per-community daily swap-to-PCE limit), and PIP-17 does not change that. The daily limit was designed to throttle PCE outflow from the protocol, not internal claim transfers between communities. Making inter-community swap consume the limit would be a separable scope decision; see *Notes*.

**Why no per-community opt-out?**
A per-community flag to disable deposit migration would re-introduce the very drift this PIP fixes, in a configuration-dependent way. The migration is mandatory.

### Backwards Compatibility

PCEToken changes are purely additive: one new external function (`executeInterCommunitySwap`), one new event (`DepositTransferredOnInterCommunitySwap`). No existing function signature changes. No storage layout changes (only existing slots are mutated).

PCECommunityToken changes are minimal at the public surface: `swapTokens`'s public signature, parameters, and visibility are unchanged. Internally, the destination-side `to.mint(sender, targetTokenAmount)` call is removed and replaced by the `pceToken.executeInterCommunitySwap(...)` call which performs both the mint and the deposit migration. No new storage.

No changes to `swapFromLocalToken`, `swapToLocalToken`, `swapFeeFromLocalToken`, ARIGATO CREATION mint logic, decay logic, or any view function.

`getDepositedPCETokens(community)` continues to return `localTokens[community].depositedPCEToken`. After PIP-17, the returned value reflects the corrected accounting (i.e., includes the effect of inter-community swaps). Off-chain readers that depend on this view will observe more accurate per-community deposits going forward. There is no migration of pre-PIP-17 historical drift: the existing on-chain state is taken as the new starting point, and accounting drifts only through future inter-community swaps.

The existing indirect path (`swapFromLocalToken` → `swapToLocalToken`) continues to work exactly as before. Its post-state on `localTokens[*].depositedPCEToken` was already correct; PIP-17 makes the direct path produce the same post-state.

Existing scripts, multisig batches, and governance proposals that invoke `swapTokens` continue to use the same external signature. Since pre-PIP-17 `swapTokens` was non-functional in practice (the destination-side mint reverted on auth), no integrations could have been relying on it for state mutation; PIP-17 makes the function functional. After PIP-17, inter-community swap from a source community whose `depositedPCEToken` is below the PCE-equivalent of the swap amount will revert.

### Test Cases

In all cases, INITIAL_FACTOR = 10^18, exchange rates and current factors are 1e18 unless noted, and BP_BASE = 10,000.

**Case 1: Standard inter-community swap.**
- Setup: communities A and B, both registered. `localTokens[A].depositedPCEToken = 100e18`, `localTokens[B].depositedPCEToken = 50e18`. `A.exchangeRate = 1e18`, both current factors = 1e18, `pceToken.lastModifiedFactor = 1e18`. User holds 30 display units of A.
- Operation: user calls `A.swapTokens(B, 30e18)`.
- Result: A burns 30e18 raw, B mints `targetTokenAmount` raw to user. PCEToken: `localTokens[A].depositedPCEToken = 70e18`, `localTokens[B].depositedPCEToken = 80e18`. Event `DepositTransferredOnInterCommunitySwap(A, B, 30e18, 30e18)` is emitted.

**Case 2: Direct path matches indirect path.**
- Setup: identical to Case 1.
- Operation 2a (indirect): user calls `pceToken.swapFromLocalToken(A, 30e18)` then `pceToken.swapToLocalToken(B, 30e18)`.
- Operation 2b (direct): from the same starting state, user calls `A.swapTokens(B, 30e18)`.
- Result: post-state `localTokens[A].depositedPCEToken` and `localTokens[B].depositedPCEToken` are equal across the two operations. (This was not true before PIP-17.)

**Case 3: Insufficient source deposit reverts.**
- Setup: `localTokens[A].depositedPCEToken = 5e18`, otherwise as Case 1. User holds 30 display units of A (e.g. via ARIGATO CREATION accumulation that inflated supply beyond deposit).
- Operation: user calls `A.swapTokens(B, 30e18)`.
- Result: revert (`"Insufficient deposited PCE token reserve at source community"`). User's A balance and B's supply unchanged. The same operation via `swapFromLocalToken(A, 30e18)` would also revert with `"Insufficient deposited PCE token reserve"` — behavior is aligned across the two paths.

**Case 4: Authorization.**
- An EOA, a generic contract, or any community token other than A calls `pceToken.executeInterCommunitySwap(A, B, recipient, 10e18, 10e18)`.
- Result: revert (`"Caller must be the from-side community token"`).

**Case 5: Same-token guard.**
- A calls `pceToken.executeInterCommunitySwap(A, A, recipient, 10e18, 10e18)` (e.g. via a misuse of `swapTokens`).
- Result: revert (`"Same-token swap"`). (Note: `PCECommunityToken.swapTokens` would already revert earlier in such a case via existing logic; this is a defense-in-depth check at the PCEToken layer.)

**Case 6: Unregistered destination.**
- B is not registered (`localTokens[B].isExists == false`). User calls `A.swapTokens(B, 30e18)`.
- Result: revert at the first registration check inside `executeInterCommunitySwap` (`"To token not registered"`). The pre-existing `to.isAllowIncomeExchange(...)` call inside `swapTokens` would in practice fail earlier when `B` is not a deployed PCECommunityToken; this is a defense-in-depth check at the PCEToken layer.

**Case 7: Dust swap (zero PCE-equivalent).**
- Setup: `A.exchangeRate` is large enough that `mulDiv(1, INITIAL_FACTOR, A.exchangeRate)` rounds to 0.
- Operation: user calls `A.swapTokens(B, 1)`. `executeInterCommunitySwap` computes `movedPceAmount == 0`, so deposit accounting is unchanged; the destination-side `mint` still proceeds with the (similarly small) `toDisplayAmount`.
- Result: deposit storage unchanged. Event `DepositTransferredOnInterCommunitySwap(A, B, 1, 0)` emitted. The outer `swapTokens`'s `targetTokenAmount > 0` check (inherited from `TokenValueOps.computeSwapAmount`) typically prevents this case from occurring at the swap layer; the zero-handling in `executeInterCommunitySwap` is defensive.

**Case 8: Multiple inter-community swaps preserve protocol-wide deposit total.**
- Setup: arbitrary mix of registered communities, deposits, and users.
- Operation: any sequence of `A.swapTokens(B, x)` calls.
- Invariant: `sum over all registered communities of localTokens[c].depositedPCEToken` is unchanged by inter-community swaps. (PIP-17 redistributes between source and destination but neither mints nor burns deposit accounting.)

### Security Considerations

**Reentrancy.** `executeInterCommunitySwap` writes to PCEToken storage (`localTokens[fromToken].depositedPCEToken`, `localTokens[toToken].depositedPCEToken`), reads exchange rate / current factor, calls `PCECommunityToken(toToken).mint(recipient, toDisplayAmount)` (an external call into a registered community token), and emits an event. The only writes-then-external-call sequence is "deposit migration → destination mint." Both the source and destination community tokens are members of the trusted registry (`localTokens[*].isExists`), and `mint` is a non-reentrant operation against PCEToken (it does not call back into PCEToken). Because `fromToken == msg.sender` is enforced and `msg.sender` is already executing, there is no new reentrancy surface beyond what `swapTokens` already exposes. Implementations SHOULD nonetheless follow checks-effects-interactions ordering for the state mutations.

**Caller authorization.** The `msg.sender == fromToken` check assumes `fromToken` is a registered community token, which is then confirmed by the `localTokens[fromToken].isExists` check. An attacker cannot deploy an arbitrary contract that masquerades as a community token to drain deposits, because the registry membership check is enforced. The destination `localTokens[toToken].isExists` check prevents redirecting deposit accounting into an unregistered address.

**Source under-collateralization surfaces correctly.** Before PIP-17, the `swapTokens` path was non-functional (reverted at the destination-side `mint` auth check), so the question of under-collateralized exits via this path did not arise in practice. With `swapTokens` made functional by PIP-17, the path is also under deposit-cover semantics: an attempt to swap from a source whose recorded deposit is below the swap's PCE-equivalent will revert with `"Insufficient deposited PCE token reserve at source community"`. This matches the indirect path (`swapFromLocalToken`) and is the correct surfacing behavior. Communities and users SHOULD be aware that direct inter-community swap from an under-collateralized source will fail.

**Front-running and ordering.** A user who observes a pending `swapTokens(B, large)` in the mempool and front-runs with `swapFromLocalToken(A, large)` may drain A's deposit and cause the pending direct swap to revert. Before PIP-17, the direct swap would have succeeded regardless. This is not a new attack vector — `swapFromLocalToken` is already first-come-first-served against A's deposit — but it now extends to inter-community swaps. The mitigation is the same as for ordinary swap-back: rely on per-block transaction ordering and the existing daily-swap limits.

**Integer arithmetic.** The `movedPceAmount` formula uses `Math.mulDiv` to perform mulDiv-then-divide safely against overflow and to avoid precision loss. Implementations MUST use `Math.mulDiv` (or equivalent) and MUST NOT use the naive `(a * b) / c` form.

**Cross-contract trust.** `executeInterCommunitySwap`'s authorization model depends on `localTokens[*].isExists` being only set for code that conforms to the PCECommunityToken contract (i.e., the registry is the trust boundary). Adding non-conformant code to the registry would be a governance failure outside the scope of this PIP, and would already break many other parts of the system. PIP-17 introduces no new risk in this dimension.

**Event coverage for off-chain accounting.** `DepositTransferredOnInterCommunitySwap` is emitted on every successful migration, including the `movedPceAmount == 0` short-circuit. Off-chain indexers can rely on this event as the single source of truth for inter-community deposit migrations and reconcile it against `Transfer`-equivalent burns/mints on the community tokens themselves.

### Notes

- **What this proposal guarantees:** for every direct inter-community swap via `PCECommunityToken.swapTokens(toToken, amount)`, the per-community PCE deposit accounting in `PCEToken.localTokens[*].depositedPCEToken` is updated atomically and consistently with the indirect A→PCE→B path. The total `sum localTokens[c].depositedPCEToken` across all registered communities is invariant under inter-community swap.
- **What this proposal does not guarantee:**
  - That per-community deposits cover per-community claims at all times. ARIGATO CREATION mints continue to inflate community-token claims without inflating deposits, and PCE BASE Token decay continues to erode claims without changing deposits. Both effects are out of scope and are properties of the existing reserve model.
  - That inter-community swap from an under-collateralized source succeeds. Such swaps will revert (Case 3). This is the intended hardening.
  - Any change to daily swap limits. Inter-community swap continues to be unconstrained by `swappedToPCEToday`.
- **Out of scope:**
  - Treating inter-community swap as consumption of the per-community daily swap-to-PCE limit. A separable design decision; if pursued, it should be a follow-up PIP that adds an explicit `recordSwapToPCE`-like call from `swapTokens`.
  - Backfilling pre-PIP-17 deposit drift. The on-chain state at the time of upgrade is the new baseline; correcting historical drift would require off-chain analysis and a one-shot governance reconciliation transaction, both of which are separable.
  - Restructuring the per-community reserve model into a global pool, or any related change to `swapFromLocalToken`'s deposit check. Considered and explicitly rejected in favor of preserving per-community accounting.
- **Dependencies:** none. PIP-17 is implementable on the current `peacecoin-protocol/core` codebase (post-PIP-14 ownership semantics if active, but no functional dependency on PIP-14).
- **Implementation reference:** `peacecoin-protocol/core` — affected files are `src/PCEToken.sol` (new function and event) and `src/PCECommunityToken.sol` (replace `to.mint(...)` in `swapTokens` with the `pceToken.executeInterCommunitySwap(...)` call). No library changes (`src/lib/ArigatoCreation.sol`, `src/lib/TokenValueOps.sol`, `src/lib/TokenSetting.sol` are untouched).
