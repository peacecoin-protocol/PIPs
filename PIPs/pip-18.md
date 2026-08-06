---
pip: 18
title: ARIGATO CREATION Cross-Community Neutralization via Protocol-wide Daily Mint Limit
proposer: CHIBA Masahiro (@nihen)
status: Draft
type: Core
created: 2026-05-01
requires: []
replaces: []
---

## PIP-18: ARIGATO CREATION Cross-Community Neutralization via Protocol-wide Daily Mint Limit

### Abstract

This proposal moves the `maxIncreaseOfTotalSupplyBp` parameter — which caps the daily ARIGATO CREATION mint volume of each community as a fraction of that community's midnight total supply — from per-community storage to a single protocol-wide value managed by PCE governance. After this change, the daily mint cap of every community is a fixed fraction of its own backing value (PCE BASE Token), so no community can grant itself a structurally larger PCE-equivalent issuance ceiling than another. Per-transaction parameters that shape *how* mints are distributed inside a community (`maxIncreaseBp`, `maxUsageBp`, `changeBp`) remain under each community's control.

### Motivation

PCE protocol invariants make the relationship between PCE Token and Community Token neutral with respect to community choice in nearly all dimensions:

- **PCE Token holdings** do not decay with time.
- **Community Token decay** affects only the *display balance*; the underlying PCE BASE Token (the swap-back claim, expressed in PCE) is not eroded by the community's own decay schedule.
- **Dilution factor** is reversibly cancelled out across `swapToLocalToken` / `swapFromLocalToken`, so it is purely a denomination choice.
- **Time decay of PCE BASE Token** is a single global rule applied to every community equally.

The remaining non-neutrality is **ARIGATO CREATION**. Today each community sets its own `maxIncreaseOfTotalSupplyBp`, which determines the per-day cap on additional Community Token minted as transfer rewards. Because this cap is denominated as a fraction of the community's own midnight supply (whose PCE-equivalent is the community's PCE BASE Token total), a community that picks a larger value can grant itself a structurally larger daily issuance ceiling per unit of backing value. From the protocol's point of view this is the only mechanism whose PCE-equivalent cap is not anchored to a single rule.

This proposal closes that gap by making `maxIncreaseOfTotalSupplyBp` a protocol-wide parameter set by governance. Everything else about ARIGATO CREATION — the per-transaction reward shape, how individual users are rewarded — remains in each community's hands as part of its local distribution policy.

### Specification

#### Overview

- A new state variable `maxIncreaseOfTotalSupplyBp` on `PCEToken` becomes the **only** value consulted when computing the daily mint cap inside `ArigatoCreation.compute`.
- The existing `TokenSetting.maxIncreaseOfTotalSupplyBp` field on each `PCECommunityToken` remains in storage (storage-layout safety) and continues to be settable through `setTokenSettings` for ABI compatibility, but is no longer read by mint logic. It becomes inert data.
- The protocol-wide value MUST be initialized in the same transaction as the upgrade. A value of `0` halts ARIGATO CREATION minting in every community (this is the existing behaviour when a community's per-community value is `0`).

#### PCEToken — new state variable

```solidity
/// @notice Protocol-wide cap on daily ARIGATO CREATION mint volume,
///         expressed as basis points of each community's midnight total supply.
///         Replaces the per-community `TokenSetting.maxIncreaseOfTotalSupplyBp`.
uint16 public maxIncreaseOfTotalSupplyBp;
```

`maxIncreaseOfTotalSupplyBp` is initialized to `0` by storage default and MUST be set by governance in the upgrade transaction (see *Upgrade procedure* below).

#### PCEToken — new function

```solidity
/// @notice Set the protocol-wide ARIGATO CREATION daily-mint cap, in basis points.
/// @dev    Only callable by the contract owner (PCE governance / Timelock).
///         Emits `MaxIncreaseOfTotalSupplyBpSet`.
/// @param  newValue New cap in basis points. MUST be `<= 10000` (i.e. `<= 100%`).
function setMaxIncreaseOfTotalSupplyBp(uint16 newValue) external onlyOwner;
```

Behaviour:

1. `require(newValue <= 10000, "Bp <= 10000")`.
2. Cache `oldValue = maxIncreaseOfTotalSupplyBp`.
3. Assign `maxIncreaseOfTotalSupplyBp = newValue`.
4. Emit `MaxIncreaseOfTotalSupplyBpSet(oldValue, newValue)`.

The new value takes effect on the next ARIGATO CREATION mint call in any community.

#### PCEToken — new event

```solidity
event MaxIncreaseOfTotalSupplyBpSet(uint16 oldValue, uint16 newValue);
```

Emitted whenever `setMaxIncreaseOfTotalSupplyBp` is called. The event is emitted unconditionally, including for no-op assignments (`oldValue == newValue`), so off-chain observers see every governance call.

#### PCECommunityToken — mint-path change

The internal call site that builds `ArigatoCreation.Params` (currently `_mintArigatoCreation` in `src/PCECommunityToken.sol`) MUST replace its `TokenSetting.maxIncreaseOfTotalSupplyBp` reference with the value read from `PCEToken`:

```solidity
// before:
//   maxIncreaseOfTotalSupplyBp: maxIncreaseOfTotalSupplyBp,
// after:
//   maxIncreaseOfTotalSupplyBp: PCEToken(pceAddress).maxIncreaseOfTotalSupplyBp(),
```

`ArigatoCreation.compute` itself is unchanged. The `TokenSetting.maxIncreaseOfTotalSupplyBp` field is no longer consulted by any mint-path code after this change.

#### PCECommunityToken — settings ABI (unchanged)

`createToken(...)`, `setTokenSettings(...)` and `getTokenSettings(...)` continue to accept and return a `maxIncreaseOfTotalSupplyBp` field at the same position. The value is still stored to preserve the existing storage layout, but is no longer used in any computation. Communities and operators MAY continue to write any value to this field; doing so has no effect on the daily mint cap.

#### Algorithms (full mint cap)

After this change, the global daily mint cap of community `c` is computed inside `ArigatoCreation.compute` as:

```
maxArigatoCreationMintToday(c) =
    midnightTotalSupply(c) × PCEToken.maxIncreaseOfTotalSupplyBp() / 10_000
```

All downstream caps (`maxArigatoCreationMintTodayForGuest = ÷10`, the per-sender share derived from `accountInfo.midnightBalance / midnightTotalSupply`, and the flat 1% guest-sender cap) follow unchanged from this base, so they automatically scale with the new global value.

The PCE-equivalent of `midnightTotalSupply(c)` is community `c`'s PCE BASE Token total. With a single protocol-wide `maxIncreaseOfTotalSupplyBp`, every community's daily mint cap is the same fraction of its own PCE BASE Token total, eliminating the only remaining cross-community non-neutrality on this dimension.

#### Upgrade procedure

The PCEToken and PCECommunityToken implementation upgrades MUST be performed atomically with the initial value setup. The single upgrade proposal MUST contain, in order:

1. Upgrade PCEToken implementation (UUPS) to the new version that adds `maxIncreaseOfTotalSupplyBp` and `setMaxIncreaseOfTotalSupplyBp`.
2. Upgrade PCECommunityToken implementation (Beacon) to the new version that reads `PCEToken.maxIncreaseOfTotalSupplyBp()` in the mint path.
3. Call `PCEToken.setMaxIncreaseOfTotalSupplyBp(initialValue)` with the governance-decided initial value.

If steps 1 and 2 ship without step 3, the protocol-wide cap is `0` and every community's ARIGATO CREATION halts. Implementations MUST NOT split the proposal across separate transactions.

#### Storage layout

- `PCEToken`: append `uint16 maxIncreaseOfTotalSupplyBp` to the end of the existing storage layout. No reordering or removal of existing slots.
- `PCECommunityToken` (and inherited `TokenSetting`): no storage layout changes. The deprecated `TokenSetting.maxIncreaseOfTotalSupplyBp` field stays in place.

### Rationale

**Why move only `maxIncreaseOfTotalSupplyBp`, not the other ARIGATO CREATION parameters?**
`maxIncreaseOfTotalSupplyBp` is the only ARIGATO CREATION parameter that determines a community's *daily issuance ceiling* in PCE-equivalent terms. The remaining parameters (`maxIncreaseBp`, `maxUsageBp`, `changeBp`) shape *how* the daily budget is distributed across individual transfers (which usage patterns earn higher reward, how strongly off-target usage is penalized). Those are local distribution-policy choices that legitimately differ from one community to another. Centralizing only the ceiling preserves community design freedom while removing the protocol-level inequity.

**Why protocol-wide rather than per-community-with-a-cap?**
A per-community cap with a global maximum (e.g. "each community sets its own value, capped at `globalMax`") would technically bound the divergence but would still allow communities to differentiate their structural ceilings up to the cap. The neutrality goal is exact equality of the *ratio* — same fraction of backing value across all communities — which only a single shared value provides.

**Why anchor the cap to `midnightTotalSupply` (PCE BASE Token total) rather than `depositedPCEToken`?**
`midnightTotalSupply` reflects the community's full backing value — actual locked PCE plus the PCE-equivalent claim created by accumulated ARIGATO CREATION mints. `depositedPCEToken` only tracks the actually-locked PCE and diverges from backing value over time as ARIGATO CREATION accumulates and inter-community swaps move PCE around. Anchoring the cap to backing value matches what the PIP is trying to neutralize (PCE-equivalent issuance ceiling per unit of backing). Anchoring to `depositedPCEToken` would couple the cap to factors unrelated to the protocol's neutrality goal (e.g. the recent history of withdrawals would shrink a community's cap even when its claims have not changed).

**Why keep the deprecated per-community field in storage?**
Removing the storage slot would break the upgrade-safe storage layout of `PCECommunityToken`. Keeping the slot — and its setter/getter ABI — has no on-chain cost beyond the unused storage write that already happens, and it lets existing tools that read `getTokenSettings()` keep working unchanged.

**Why require atomic initialization with the upgrade?**
A new `uint16` defaults to `0`. With `maxIncreaseOfTotalSupplyBp == 0`, `ArigatoCreation.compute` returns `(0, _)` for every call, halting ARIGATO CREATION minting across every community. Splitting initialization into a follow-up transaction would create a mint outage between deployment and the second transaction. Bundling both into one governance proposal is the only safe option.

**Why not bound `setMaxIncreaseOfTotalSupplyBp` more tightly than `<= 10000`?**
The current per-community field is a `uint16` with the same `<= 10000` semantics implied by the BP convention. Introducing a tighter bound (e.g. `<= 500` for "max 5%") would freeze a value-judgement into the contract that governance can already enforce. The `<= 10000` bound is purely a sanity check that the value fits a basis-point interpretation.

### Backwards Compatibility

**Behavioural changes:**

- The per-community `maxIncreaseOfTotalSupplyBp` value stored in each community's `TokenSetting` storage is no longer consulted when computing the daily mint cap. Communities whose previous local value differed from the new protocol-wide value will see their effective daily cap change immediately at upgrade.
- Tooling that surfaces "this community's daily mint cap" must read `PCEToken.maxIncreaseOfTotalSupplyBp()` instead of the per-community value. Reading the per-community field still returns the previously stored number, but that number no longer drives mint behaviour.

**Non-breaking:**

- `createToken`, `setTokenSettings`, and `getTokenSettings` retain the same signatures, parameter order, and returned tuple. Existing scripts and frontends do not need ABI changes. Pass-through values are stored but do not affect mint logic.
- Per-transaction reward shaping (`maxIncreaseBp`, `maxUsageBp`, `changeBp`) is unchanged. Communities continue to set these locally.
- Decay (`afterDecreaseBp`, `decreaseIntervalDays`), swap (`swapFromLocalToken`, `swapToLocalToken`, `swapTokens`), `Transfer`, allowance, voucher, and EIP-3009 paths are not modified.
- PCEToken storage layout is extended only by appending one new slot. PCECommunityToken storage layout is unchanged.

### Test Cases

All examples below assume `BP_BASE = 10_000` and a freshly-upgraded protocol where governance has set `PCEToken.maxIncreaseOfTotalSupplyBp = 100` (i.e. 1%). All Community Token amounts are raw (pre-factor-multiplication) values to match `midnightTotalSupply`.

**Case 1: Two communities of equal size and equal cap ratio**
- Community A: `midnightTotalSupply = 10_000 ether`
- Community B: `midnightTotalSupply = 10_000 ether`
- Both communities' previously-stored per-community `maxIncreaseOfTotalSupplyBp` was different (A: 100, B: 50). After upgrade, both are read from PCEToken (= 100).
- `maxArigatoCreationMintToday(A) = 100 ether`, `maxArigatoCreationMintToday(B) = 100 ether`
- Result: identical caps across communities. The previously divergent local values no longer have any effect.

**Case 2: Communities of different sizes, same cap ratio**
- Community A: `midnightTotalSupply = 10_000 ether` (PCE BASE Token total ≈ 10_000 PCE)
- Community B: `midnightTotalSupply = 1_000 ether` (PCE BASE Token total ≈ 1_000 PCE)
- `maxArigatoCreationMintToday(A) = 100 ether (≈ 100 PCE worth)`
- `maxArigatoCreationMintToday(B) = 10 ether (≈ 10 PCE worth)`
- Result: same fraction of backing value (1%) per community.

**Case 3: Governance changes the cap**
- Initial state: `PCEToken.maxIncreaseOfTotalSupplyBp = 100` (1%)
- Governance executes `setMaxIncreaseOfTotalSupplyBp(50)` (0.5%)
- Event: `MaxIncreaseOfTotalSupplyBpSet(100, 50)`
- Next ARIGATO CREATION mint in any community uses the new 0.5% cap.
- No state change in any `PCECommunityToken` is required.

**Case 4: Cap of 0 halts minting**
- `PCEToken.maxIncreaseOfTotalSupplyBp = 0`
- Any transfer that would normally trigger ARIGATO CREATION still completes normally as a transfer; the additional mint amount is `0`.
- This matches today's behaviour when a community's local `maxIncreaseOfTotalSupplyBp == 0`.

**Case 5: Per-community field still settable but inert**
- Owner of community A calls `setTokenSettings(..., maxIncreaseOfTotalSupplyBp = 5_000, ...)` (i.e. 50%).
- The value is written to community A's storage; `getTokenSettings()` reflects it.
- A subsequent transfer in community A still mints under the protocol-wide cap (e.g. 1% if PCEToken's value is 100). The local 50% value has no effect.

**Case 6: Reverts on out-of-range input**
- Governance executes `setMaxIncreaseOfTotalSupplyBp(10_001)`.
- The call MUST revert with `"Bp <= 10000"`.

**Case 7: Non-governance caller cannot set the cap**
- Any non-owner address calls `setMaxIncreaseOfTotalSupplyBp(50)`.
- The call MUST revert (OpenZeppelin `OwnableUnauthorizedAccount` error).

### Security Considerations

**Centralization of cap control**: After this PIP, the daily mint ceiling of every community is set by a single governance-controlled value. This is by construction — the goal of the PIP is to remove a per-community degree of freedom. The standard PCE governance protections (Timelock delay between proposal and execution, on-chain quorum and voting period set by the Governor) constrain abrupt or hostile changes. Implementations SHOULD ensure the Timelock delay is sufficient for communities to react to a proposed change.

**Atomic-initialization invariant**: As noted under *Upgrade procedure*, splitting the upgrade and the initial `setMaxIncreaseOfTotalSupplyBp` call into separate transactions causes a protocol-wide mint outage between them. Governance MUST bundle both into a single proposal. The proposal text SHOULD explicitly include the initial value so reviewers can verify it.

**Read path coupling**: `PCECommunityToken._mintArigatoCreation` now performs an external `STATICCALL` to `PCEToken` for every transfer that triggers ARIGATO CREATION. This adds approximately one extra cold storage read of cost to mint-eligible transfers, paid by the relayer (for meta-transactions) or the sender (for direct transfers). The cost is negligible relative to the existing ERC-20 transfer and ARIGATO CREATION accounting.

**Frontrunning the cap-change**: A user observing a pending governance proposal that lowers `maxIncreaseOfTotalSupplyBp` could attempt to execute mint-eligible transfers ahead of the change to consume more of the old higher cap. This is identical in shape to current behaviour where a community owner could lower the local value, and is bounded by the per-day cap itself (the user cannot mint more than today's cap regardless). No additional on-chain protection is proposed; implementations MAY surface upcoming changes off-chain.

**Reverse migration**: This PIP does not provide a path back to per-community `maxIncreaseOfTotalSupplyBp`. If a future PIP wants per-community values to govern again, it would need to introduce a separate read switch and reverse the mint-path change. The deprecated storage slot is preserved precisely to keep this option open without further upgrade-safety risk.

### Notes

- **Scope of impact**: PCEToken (UUPS) and PCECommunityToken (Beacon) both require an upgrade. No community-level migration script is required beyond the bundled `setMaxIncreaseOfTotalSupplyBp` call.
- **Not in scope**: Choosing the initial value of `maxIncreaseOfTotalSupplyBp` and the cadence at which governance reviews it. Both are governance decisions to be made alongside this PIP's adoption (see *Open governance decisions* below).
- **Not in scope (cross-community neutrality, individual level)**: This PIP does not equalize per-transfer reward rates across communities. Differences in `maxIncreaseBp`, `maxUsageBp`, and `changeBp` continue to make the same transfer earn different ARIGATO CREATION amounts in different communities. That is treated as a community-internal distribution policy and explicitly retained.
- **Not in scope (PCE BASE Token vs `depositedPCEToken`)**: The structural divergence between PCE BASE Token total (the conceptual backing value) and `depositedPCEToken` (the actually-locked PCE) is a separate, longer-running concern not addressed here. This PIP anchors the cap to `midnightTotalSupply` (which corresponds to PCE BASE Token), so its neutrality property holds independently of how `depositedPCEToken` evolves.
- **Open governance decisions** (to be made alongside adoption):
  1. The initial value of `maxIncreaseOfTotalSupplyBp` (likely informed by a survey of currently configured per-community values).
  2. The review cadence and the metrics governance will consult before changing it.
  3. Communication to existing community operators about the deprecation of the per-community setting.
- **Related repos**: [peacecoin-protocol/core](https://github.com/peacecoin-protocol/core), [peacecoin-protocol/PIPs](https://github.com/peacecoin-protocol/PIPs).
- **Source document**: <https://github.com/nihen/nihen.github.io/blob/main/peacecoin-protocol/arigato-neutrality/README.md>
- **Dependencies**: None. Compatible with PIP-13, PIP-15, PIP-16, PIP-17 storage and ABI.
