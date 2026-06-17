---
pip: 21
title: Path-Consistent Community Token Decay Factor
proposer: CHIBA Masahiro (@nihen)
status: Draft
type: Core
created: 2026-06-17
requires: []
replaces: []
---

## PIP-21: Path-Consistent Community Token Decay Factor

### Abstract

This proposal fixes the current behavior in which community-token decay depends on how often `updateFactorIfNeeded()` is executed. It changes the multi-period decay calculation of `PCECommunityToken.getCurrentFactor()` so that the per-period community-token decay rate is exponentiated at WAD precision (`1e18`) instead of basis-point precision (`10000`), keeping batched multi-period calculation aligned with period-by-period materialization up to WAD rounding. It also adds the missing `afterDecreaseBp <= 10000` validation to `PCECommunityToken.setTokenSettings` and requires governance to materialize the current factor of every existing token before the implementation upgrade.

### Motivation

Community-token decay should be path-consistent: for the same settings and the same elapsed period count, the result should not materially depend on transaction frequency or on how often `updateFactorIfNeeded()` is called. The current implementation violates this invariant. Materializing the factor once per decay period and computing several elapsed periods in one `getCurrentFactor()` call can produce different results.

Community-token decay is configured by `afterDecreaseBp`, for example `9980` for a 99.8% remaining balance per decay period. The current implementation converts the final result to WAD, but exponentiation-by-squaring squares the rate while it is still represented in basis points:

```solidity
uint256 rate = afterDecreaseBp;
uint256 base = BP_BASE;
rate = Math.mulDiv(rate, rate, base);
```

For `afterDecreaseBp = 9980`, the first squaring computes `9980 * 9980 / 10000 = 9960` after integer truncation, while the mathematically configured two-period value is `0.998^2 = 0.996004`. Under the current implementation, materializing the factor once per decay period and computing several elapsed periods in one `getCurrentFactor()` call are therefore not equivalent: the batched calculation uses BP-truncated squared rates. The more periods that are batched into one calculation, the more over-decay accumulates relative to the configured rate.

### Specification

#### Scope

This proposal changes only the community-token decay factor calculation and `setTokenSettings` validation. The primary goal is to remove the current behavior where multi-period decay materially depends on transaction cadence / materialization cadence; WAD precision is the implementation mechanism. It does not change PCE Token decay, swap formulas, ARIGATO CREATION, voucher logic, token balances, storage layout, or event schemas.

#### Constants

Existing constants keep their current meanings:

```solidity
uint256 public constant INITIAL_FACTOR = 10 ** 18;
uint256 public constant BP_BASE = 10_000;
```

`INITIAL_FACTOR` is the WAD base used by display/raw balance conversion. `BP_BASE` is the input scale of basis-point configuration values.

#### PCECommunityToken.getCurrentFactor

The implementation MUST convert `afterDecreaseBp` to WAD precision before exponentiation-by-squaring:

```solidity
function getCurrentFactor() public view returns (uint256);
```

The multi-period section MUST be equivalent to:

```solidity
uint256 times = elapsed / decreaseIntervalDays;
uint256 factor = lastModifiedFactor;
uint256 rate = Math.mulDiv(afterDecreaseBp, INITIAL_FACTOR, BP_BASE);
uint256 base = INITIAL_FACTOR;
uint256 n = times;

while (n > 0) {
    if (n % 2 == 1) {
        factor = Math.mulDiv(factor, rate, base);
    }
    rate = Math.mulDiv(rate, rate, base);
    n /= 2;
}

return factor;
```

All existing guard behavior remains unchanged:

1. If `lastModifiedFactor == 0`, return `0`.
2. If `decreaseIntervalDays == 0`, return `lastModifiedFactor`.
3. If no UTC day boundary has passed since `lastDecreaseTime`, return `lastModifiedFactor`.
4. If the elapsed day count is less than `decreaseIntervalDays`, return `lastModifiedFactor`.

#### PCECommunityToken.setTokenSettings

The following existing function MUST reject a decay setting above 100%:

```solidity
function setTokenSettings(
    uint256 _decreaseIntervalDays,
    uint16 _afterDecreaseBp,
    uint16 _maxIncreaseOfTotalSupplyBp,
    uint16 _maxIncreaseBp,
    uint16 _maxUsageBp,
    uint16 _changeBp,
    ExchangeAllowMethod _incomeExchangeAllowMethod,
    ExchangeAllowMethod _outgoExchangeAllowMethod,
    address[] calldata _incomeTargetTokens,
    address[] calldata _outgoTargetTokens
) public override onlyOwner;
```

Required behavior:

```solidity
require(_afterDecreaseBp <= BP_BASE, "After decrease bp <= 10000");
```

`PCEToken.createToken` already performs equivalent validation before calling `setTokenSettings`; this proposal makes direct owner updates obey the same invariant.

#### Version

`PCECommunityToken.version()` MUST return `"1.0.17"` after this change.

`PCEToken.version()` is unchanged by this proposal.

#### Storage and state changes

No storage variables are added, removed, reordered, or retyped.

The proposal does not migrate balances, `lastModifiedFactor`, `lastDecreaseTime`, `rebaseFactor`, token settings, or any per-account storage. The only state changes during execution are the normal `updateFactorIfNeeded()` writes performed by the governance upgrade procedure below.

#### Events and logs

No new events are introduced. Existing functions keep their current event behavior.

#### Governance upgrade procedure

For existing deployments, the governance proposal MUST execute the factor update calls before the community-token implementation upgrade:

1. Call `PCEToken.updateFactorIfNeeded()`.
2. For every community token returned by `PCEToken.getTokens()` at proposal preparation time, call `PCECommunityToken(token).updateFactorIfNeeded()`.
3. Upgrade the `PCECommunityToken` beacon implementation to the implementation containing this proposal.

This order materializes all pending decay under the pre-upgrade formula before the new formula becomes active. From the first decay period after the upgrade, the WAD-precision formula applies.

If governance also upgrades `PCEToken` in the same proposal for release packaging, that upgrade MUST NOT replace step 1 and MUST NOT be ordered before the update calls unless the new `PCEToken` implementation is behaviorally identical for `updateFactorIfNeeded()`.

### Rationale

#### Use WAD precision for the exponentiation base

Community-token factors are stored and applied at WAD precision. Converting `afterDecreaseBp` to WAD before exponentiation keeps intermediate squared rates on the same scale as the stored factor and removes the repeated basis-point truncation. This is the smallest code change that makes `afterDecreaseBp = 9980` mean `0.998^n` for `n` periods.

#### Keep O(log n) exponentiation

The implementation keeps exponentiation-by-squaring instead of looping over every elapsed period. This preserves the gas and runtime characteristics of the existing implementation while correcting the precision of the intermediate values.

#### Do not retroactively compensate users

The proposal corrects the formula prospectively. It does not mint compensation, rewrite raw balances, or reinterpret past state. The contract has already exposed balances under the current formula, and a retroactive compensation mechanism would require a separate policy decision and additional state/accounting design.

#### Materialize factors before upgrade

Calling `updateFactorIfNeeded()` before the beacon upgrade creates a clean boundary: all pending decay up to the execution time uses the pre-upgrade formula, and all future decay uses the fixed formula. Without this step, an old unmaterialized decay window would be recalculated by the fixed formula after the upgrade, changing balances at the upgrade boundary without an explicit factor-update event from the pre-upgrade implementation.

#### Add `setTokenSettings` validation

`afterDecreaseBp > 10000` represents growth rather than decay and violates the existing invariant already enforced in `PCEToken.createToken`. Direct owner updates through `setTokenSettings` must obey the same invariant. This also keeps the corrected exponentiation bounded by `rate <= INITIAL_FACTOR`.

### Backwards Compatibility

The ABI is unchanged. Function names, parameters, return values, storage layout, and events remain compatible.

Existing community tokens keep their stored settings and balances. After the governance procedure, their `lastModifiedFactor` and `lastDecreaseTime` are updated exactly as a normal pre-upgrade `updateFactorIfNeeded()` call would update them. Future decay then uses the corrected formula.

The behavior change is visible in `getCurrentFactor()`, `balanceOf()`, `totalSupply()`, swap calculations, and transfer amounts expressed in display balance. The direction of the change is that future balances are no longer over-decayed by basis-point truncation.

`setTokenSettings` becomes stricter for `_afterDecreaseBp > 10000`. This is not a breaking change for valid decay configurations because `PCEToken.createToken` already rejects the same invalid range.

### Test Cases

#### Two periods with 99.8% remaining rate

Configuration:

- `lastModifiedFactor = 1_000_000_000_000_000_000`
- `decreaseIntervalDays = 7`
- `afterDecreaseBp = 9980`
- `times = 2`

Expected fixed factor:

```text
0.998^2 * 1e18 = 996_004_000_000_000_000
```

Current basis-point squaring produces:

```text
996_000_000_000_000_000
```

#### Twelve periods with 99.8% remaining rate

Configuration:

- `lastModifiedFactor = 1_000_000_000_000_000_000`
- `decreaseIntervalDays = 7`
- `afterDecreaseBp = 9980`
- `times = 12`

Expected fixed factor:

```text
976_262_247_894_715_033
```

Current basis-point-precision factor:

```text
976_128_000_000_000_000
```

A holder with `1,000,000` display tokens at the starting factor would display:

```text
fixed:  976,262.247894715033 tokens
current BP: 976,128.000000000000 tokens
diff:   134.247894715033 tokens
```

#### Fifty-two periods with 99.8% remaining rate

Configuration:

- `lastModifiedFactor = 1_000_000_000_000_000_000`
- `decreaseIntervalDays = 7`
- `afterDecreaseBp = 9980`
- `times = 52`

Expected fixed factor:

```text
901_131_449_719_291_632
```

Current basis-point-precision factor:

```text
900_329_954_560_000_000
```

A holder with `1,000,000` display tokens at the starting factor would display:

```text
fixed:  901,131.449719291632 tokens
current BP: 900,329.954560000000 tokens
diff:   801.495159291632 tokens
```

#### No decay setting

If `decreaseIntervalDays = 0`, `getCurrentFactor()` MUST return `lastModifiedFactor` without reading or exponentiating `afterDecreaseBp`.

#### Full retention setting

If `afterDecreaseBp = 10000`, `getCurrentFactor()` MUST return `lastModifiedFactor` for every elapsed period count.

#### Invalid owner update

`setTokenSettings(..., _afterDecreaseBp = 10001, ...)` MUST revert with:

```text
After decrease bp <= 10000
```

### Security Considerations

Implementations MUST use `Math.mulDiv` for both `factor * rate / base` and `rate * rate / base`. With `_afterDecreaseBp <= BP_BASE`, the corrected `rate` is bounded by `INITIAL_FACTOR`, so repeated squaring cannot increase the rate above WAD. `Math.mulDiv` also preserves safe 512-bit intermediate multiplication semantics.

Governance MUST execute `updateFactorIfNeeded()` for PCE and all known community tokens before the beacon upgrade. The token list SHOULD be generated from `PCEToken.getTokens()` close to proposal creation and reviewed before execution. If new community tokens can be created between proposal creation and execution, governance SHOULD either pause creation through operational policy for the proposal window or include any newly created tokens in a follow-up update before relying on a clean accounting boundary.

The change makes future displayed balances higher than they would have been under the pre-upgrade over-decay formula. This is a correction toward the configured decay rate, not a mint, and it does not alter raw balances or reserve accounting.

### Notes

Out of scope:

- Retroactive compensation for historical over-decay.
- Raw balance migration.
- Changes to PCE Token decay.
- Changes to swap-rate, ARIGATO CREATION, voucher, or bridge formulas.
- A new event for factor updates.

### Copyright

Copyright and related rights waived via [CC0](https://creativecommons.org/publicdomain/zero/1.0/).
