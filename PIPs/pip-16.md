---
pip: 16
title: Owner-controlled Token Value Operations (supersedes PIP-12)
proposer: CHIBA Masahiro (@nihen)
status: Draft
type: Core
created: 2026-04-26
requires: []
replaces: [12]
---

## PIP-16: Owner-controlled Token Value Operations

### Abstract

This proposal supersedes PIP-12 with a simplified design. It adds two operations that adjust the swap rate (`exchangeRate`) of a community token: `increaseTokenValue` and `splitToken`. Both live on the PCEToken contract — the same contract that already hosts the swap operations between PCE and community tokens — and are gated to the community owner (the owner of the community's PCECommunityToken). The dedicated `RATE_MANAGER_ROLE` and `treasuryWallet` storage proposed in PIP-12 are removed; PCE reserves are pulled directly from the caller, who is the community owner. Communities that want to govern these operations through a DAO simply transfer ownership of their community token to a Governor + Timelock setup, which makes the Timelock both the operation authority and the on-chain treasury for the community.

### Motivation

PIP-12 introduced a `RATE_MANAGER_ROLE` with a custom mapping-based role system and a separate `treasuryWallet` address, motivated by the assumption that operation authority and PCE custody could live at different addresses, and that the community owner authority needed to be split off. Subsequent design review revealed that:

1. Communities that govern these operations through a DAO inevitably converge to `community owner = Governor's Timelock = community treasury = PCE custody`. In that pattern there is no benefit to splitting the community owner from a separate `RATE_MANAGER` or to keeping a separate `treasuryWallet`.
2. The split adds storage variables (`treasuryWallet`, `_roles`, `RATE_MANAGER_ROLE`), administrative functions (`initializeTreasury` / `setTreasuryWallet` / `grantRateManagerRole` / `revokeRateManagerRole` / `hasRole`), and bytecode to a contract that has been near the Polygon 32 KB limit (see commit `cc07abf`). The cost is real; the benefit was hypothetical.

PIP-12 was cancelled on the Polygon Timelock before its implementation switch executed (cancellation tx: `0xe4ed89b0846ce643b29b528e1e2fbe247a51867235ba88cf1760fd944661e1fb` on 2026-04-26). This proposal redesigns the same economic mechanism in the simplest form that meets the actual requirement.

This proposal enables:
- The community owner (or, after `transferOwnership`, the community's Governor + Timelock) to add PCE reserves to increase token value
- The same authority to expand token supply via proportional distribution to all holders (token split)
- Adjustment of the swap rate through reserve management and supply expansion

### Specification

#### Interface

The following interfaces are added to PCEToken. PCECommunityToken receives one new internal-coordination function (`applyRebase`).

**PCEToken — new functions:**

```solidity
function increaseTokenValue(address communityToken, uint256 pceAmount) external;
function splitToken(address communityToken, uint256 mintAmount) external;
```

Both functions verify the caller is the community owner via `OwnableUpgradeable(communityToken).owner() == msg.sender`. The community token must be registered (`localTokens[communityToken].isExists == true`).

**PCECommunityToken — new functions:**

```solidity
function applyRebase(uint256 newFactor) external;
```

`applyRebase` is gated to `msg.sender == pceAddress` (i.e. only PCEToken can call it). It is the internal-coordination function PCEToken uses during `splitToken` to update the community token's `rebaseFactor`. It is not a client-facing entrypoint and follows the same convention as the existing `mint` / `burnByPCEToken` / `recordSwapToPCE` functions, which are similarly gated to only accept calls from PCEToken. Clients (community owners, multisigs, Timelocks, etc.) interact only with `PCEToken.increaseTokenValue` and `PCEToken.splitToken`.

#### Storage

The following storage variable is added to `PCECommunityToken`:

```solidity
uint256 public rebaseFactor; // initialized lazily; treated as INITIAL_FACTOR (1e18) when zero
```

All new variables MUST be appended to the end of existing storage. Existing storage slots MUST NOT be modified.

PIP-12 introduced `treasuryWallet` and a `_roles` mapping. PIP-12's Beacon implementation switch never executed on the Production chain, but DEV had been upgraded directly to a v15 implementation that committed those slots. To allow a single v16 implementation to upgrade both DEV and Production cleanly, the v16 implementation declares `__deprecated_treasuryWallet` and `__deprecated_roles` as private placeholders at the original PIP-12 slot positions (annotated `@custom:oz-renamed-from` for OpenZeppelin upgrade-safety validation). They are unused by PIP-16 logic.

PCEToken adds no new storage.

#### Events

```solidity
event TokenValueIncreased(
    address indexed communityToken,
    uint256 pceAmount,
    uint256 oldExchangeRate,
    uint256 newExchangeRate
);
event TokenSplit(
    address indexed communityToken,
    uint256 mintAmount,
    uint256 oldExchangeRate,
    uint256 newExchangeRate,
    uint256 oldRebaseFactor,
    uint256 newRebaseFactor
);
```

Both events are emitted from PCEToken so the `communityToken` index identifies which community a given operation applies to. The PIP-12 events `TreasuryWalletSet`, `RateManagerRoleGranted`, and `RateManagerRoleRevoked` are removed.

#### Token Value Increase

Transfers PCE from the community owner directly to the PCEToken contract and adjusts the swap rate downward, increasing the per-token PCE value of every existing holder.

1. Verify `localTokens[communityToken].isExists == true` and `OwnableUpgradeable(communityToken).owner() == msg.sender`.
2. Transfer `pceAmount` of PCE from `msg.sender` to the PCEToken contract via the contract's internal `_transfer`. No prior `approve` is required because PCEToken is itself the PCE ERC20 contract and the caller is verified to be the community owner.
3. Increase `depositedPCEToken` by `pceAmount`.
4. Adjust `exchangeRate`: `newRate = oldRate * oldDeposited / (oldDeposited + pceAmount)`.
5. No community tokens are minted.
6. Emit `TokenValueIncreased(communityToken, pceAmount, oldRate, newRate)`.

#### Token Split

Adjusts the rebase factor to mint new community tokens and distributes them proportionally to all existing holders. PCE reserves are NOT withdrawn. The swap rate is adjusted upward to reflect the increased token supply, decreasing the per-token PCE value.

1. Verify `localTokens[communityToken].isExists == true` and `OwnableUpgradeable(communityToken).owner() == msg.sender`. Verify the community token's current `totalSupply()` is greater than zero.
2. `mintAmount` is specified in the display (post-rebase) unit of the community token.
3. Compute `newRebaseFactor = oldRebaseFactor * (totalDisplaySupply + mintAmount) / totalDisplaySupply`. (`oldRebaseFactor` is treated as `INITIAL_FACTOR` when zero, so the first split on a never-split community works correctly.)
4. Call `PCECommunityToken.applyRebase(newRebaseFactor)` on the community token to commit the new factor.
5. All holders' display balances increase proportionally because the community token's `balanceOf` / `totalSupply` computation incorporates `rebaseFactor`.
6. Adjust `exchangeRate`: `newRate = oldRate * newRebaseFactor / oldRebaseFactor`.
7. `depositedPCEToken` is unchanged.
8. No PCE moves.
9. Emit `TokenSplit(communityToken, mintAmount, oldRate, newRate, oldRebaseFactor, newRebaseFactor)`.

The display balance computation incorporates the rebase factor: `displayBalance = rawBalance * INITIAL_FACTOR / getCurrentFactor() * rebaseFactor / INITIAL_FACTOR` (with `rebaseFactor` treated as `INITIAL_FACTOR` when zero, so existing tokens behave identically until a split happens).

#### Symmetry

| | Token Value Increase | Token Split |
|---|---|---|
| PCE flow | community owner → PCEToken contract | none |
| `depositedPCEToken` | increased | unchanged |
| `exchangeRate` | adjusted down (token value ↑) | adjusted up (token value ↓) |
| Token supply | unchanged | increased (via rebase factor) |
| `rebaseFactor` | unchanged | increased |
| Holder balances | unchanged | all increased proportionally |

#### Authority

Both operations are gated by `OwnableUpgradeable(communityToken).owner() == msg.sender`. There is no separate role:

- For self-managed communities, the community owner is the address that called `createToken` (typically an EOA or multisig); the community owner calls these functions on PCEToken directly.
- For DAO-managed communities, the community deploys a Governor + TimelockController and calls `transferOwnership(timelock)` on the community token. The community's Timelock then becomes the operation authority and the on-chain treasury that holds PCE for `increaseTokenValue`. A proposal calls `PCEToken.increaseTokenValue(communityToken, amount)` directly; no `approve` is required because the Timelock is the caller and PCEToken pulls PCE from `msg.sender`.
- Migration between the two modes is performed via standard `transferOwnership` on the community token. No PIP-specific migration step is required.

### Rationale

**Why are the operations on PCEToken instead of PCECommunityToken?**
The state mutated by these operations — `localTokens[communityToken].depositedPCEToken` and `.exchangeRate` — lives on PCEToken, alongside the existing swap functions (`swapFromLocalToken`, `swapToLocalToken`, `swapFeeFromLocalToken`). Locating the new operations next to the state and the related swap logic produces a single, coherent surface for all PCE↔community economic operations. It also matches the existing protocol pattern in which PCEToken privileged-calls back into the community token (`mint`, `burnByPCEToken`, `recordSwapToPCE`); `applyRebase` is a new entry in that same pattern. PIP-12 originally placed these operations on the community token because the design centred on a per-community treasury wallet and role system. With those concepts removed, the community-token location no longer has a justification; consolidating on PCEToken eliminates the `addReserve` cross-contract bounce, removes the standing-`approve` requirement, and reduces community-token bytecode (relevant to the Polygon 32 KB ceiling).

**Why `onlyOwner`-equivalent authorisation instead of a dedicated role?**
The community owner of PCECommunityToken is already community-scoped (assigned at `createToken` to the creator). Communities that need governance simply set the community owner to a Governor's Timelock; communities that operate informally keep the community owner on a multisig or EOA. A separate `RATE_MANAGER_ROLE` is only beneficial if the community owner and the rate-changing authority are intended to live at different addresses, which has no concrete operational use case. Aligning all governable operations under the same authority keeps the mental model and the DAO proposal flow coherent.

**Why no `treasuryWallet`?**
The PCE used for token value increases is held wherever the community's PCE custody lives. When the community owner is a DAO Timelock, that Timelock is also the caller of `increaseTokenValue`, so the community owner is the correct and only address to pull PCE from. A separate `treasuryWallet` would only matter if the rate-changing authority and the PCE custodian were intentionally different addresses; in practice they are not.

**Why no prior `approve` from the community owner?**
PCEToken is the PCE ERC20 contract itself. When the community owner calls `PCEToken.increaseTokenValue(...)` directly, the call passes through PCEToken's own ownership context, and PCEToken can use its internal `_transfer(msg.sender, address(this), pceAmount)` without an external `transferFrom` round-trip. This is the same pattern used by the existing `swapFromLocalToken` and `swapToLocalToken` flows, which transfer PCE in and out without requiring users to issue `approve` calls.

**Why use a factor-based rebase rather than iterating holders?**
Iterating all holders on-chain is gas-prohibitive and would require maintaining an enumerable holder set. A factor-based approach achieves O(1) gas regardless of holder count and is consistent with PCECommunityToken's existing demurrage factor pattern.

**Why adjust `exchangeRate` for token value increases?**
Minting would dilute existing holders. Adjusting `exchangeRate` changes the per-token PCE value uniformly across all community tokens without touching balances or total supply.

**Why use rebase distribution rather than PCE withdrawal for token splits?**
PCE withdrawal to a treasury would deplete the reserves backing the community token. Rebase keeps PCE reserves intact and instead expands token supply proportionally. No value leaves the system; the economic effect is uniform supply expansion (analogous to a stock split), preserving each holder's ownership share while adjusting the per-token value.

**Comparison to PIP-12.**
| | PIP-12 | PIP-16 (this) |
|---|---|---|
| Authority for value ops | dedicated `RATE_MANAGER_ROLE` (mapping-based) | community owner check on PCEToken |
| Operation host contract | PCECommunityToken | PCEToken |
| Cross-contract round-trip | `community → addReserve → community` | none (PCEToken handles directly) |
| PCE source | `treasuryWallet` (separate slot) | community owner (= caller of PCEToken) |
| Standing `approve` from caller | required | not required |
| Storage added (`PCECommunityToken`) | `treasuryWallet`, `_roles`, `RATE_MANAGER_ROLE`, `rebaseFactor` | `rebaseFactor` only |
| Admin functions added | `initializeTreasury`, `setTreasuryWallet`, `grantRateManagerRole`, `revokeRateManagerRole`, `hasRole` | none |
| Events added | 5 (split between contracts) | 2 (both on PCEToken, indexed by `communityToken`) |
| Migration path to DAO governance | grant role + set treasury | `transferOwnership` (already supported) |
| Bytecode impact (community token) | larger (relevant given Polygon 32 KB ceiling) | smaller than PIP-12 by ~1.5 KB |

### Backwards Compatibility

All changes are additive: new functions on PCEToken, one new internal-coordination function on PCECommunityToken (`applyRebase`), one new storage variable (`rebaseFactor`) appended to existing storage, two new events. Existing function signatures and behaviour are unchanged.

PIP-12 was cancelled on the Polygon Timelock before its Beacon implementation switch executed, so its proposed storage layout was never committed on Production. PIP-16 starts from the current Production storage layout with no conflicts. DEV had been upgraded directly to a v15 implementation that did commit the PIP-12 slots; v16 retains those slots as `__deprecated_*` private placeholders so a single v16 implementation upgrades both environments cleanly.

`rebaseFactor` is treated as `INITIAL_FACTOR` (1e18) when zero, so PCECommunityTokens that have never been split continue to compute display balances identically to before the upgrade.

### Test Cases

All examples use `INITIAL_FACTOR = 10^18`.

**Case 1: Token value increase**
- Initial: `depositedPCEToken = 100e18`, `exchangeRate = 1e18`, community owner holds 100e18 PCE
- Operation: community owner calls `pceToken.increaseTokenValue(communityToken, 50e18)`
- Result: `depositedPCEToken = 150e18`, `exchangeRate = 1e18 * 100e18 / 150e18 ≈ 0.667e18`
- Effect: Per-token PCE value of every holder increases. Community owner's PCE balance decreases by 50e18.

**Case 2: Token split (supply expansion)**
- Initial: `totalDisplaySupply = 200e18`, `exchangeRate = 1e18`, `rebaseFactor = 1e18`
- Operation: community owner calls `pceToken.splitToken(communityToken, 50e18)`
- Result: `rebaseFactor = 1.25e18`, `exchangeRate = 1.25e18`
- Effect: All holders' display balances increase by 25 %. Per-token PCE value decreases. `depositedPCEToken` is unchanged. Each holder's PCE-equivalent total is unchanged.

**Case 3: Token split preserves ownership shares**
- Initial: Alice 100 (50 %), Bob 100 (50 %), `totalDisplaySupply = 200e18`
- Operation: `splitToken(communityToken, 100e18)`
- Result: Alice 150 (50 %), Bob 150 (50 %), `totalDisplaySupply = 300e18`

**Case 4: Unauthorised access (non-owner caller)**
- A non-owner account calls `pceToken.increaseTokenValue(communityToken, 10e18)` or `pceToken.splitToken(communityToken, 10e18)`
- Result: revert (`Only community owner`)

**Case 5: applyRebase access control**
- A non-PCEToken caller calls `communityToken.applyRebase(2e18)`
- Result: revert (`Only PCE token`)

**Case 6: DAO governance flow**
- Community owner of the PCECommunityToken is a TimelockController.
- A Governor proposal contains a single call: `pceToken.increaseTokenValue(communityToken, amount)`.
- The call executes under the Timelock as `msg.sender`, which equals `OwnableUpgradeable(communityToken).owner()`, so authorisation passes.
- Result: PCE leaves the Timelock (the community's on-chain treasury) and is added to the reserves; `exchangeRate` adjusts.

### Security Considerations

**Compromise of the community owner.** A compromised community owner can call `splitToken` repeatedly to dilute the per-token PCE value, or can drain its own PCE via `increaseTokenValue`. PCE never leaves the system as a result of `splitToken`, so the attack surface for value-extraction is bounded by the community owner's own PCE balance. Communities SHOULD use a multisig or DAO Governor + Timelock for the community owner role. The governance model is out of scope for this proposal.

**Front-running.** A user MAY attempt to front-run a pending `splitToken` transaction with a `swapFromLocalToken` at the pre-split rate. Because no PCE leaves reserves on a split, the impact is limited to rate arbitrage. Mitigation strategies (commit-reveal, private mempools, etc.) are out of scope.

**Reentrancy.** `increaseTokenValue` performs an internal `_transfer` (no external call from PCEToken). `splitToken` performs one external call into the registered PCECommunityToken (`applyRebase`). The PCECommunityToken set is governed and constrained to the registered community token implementations, so the external call cannot be redirected to arbitrary code. Implementations SHOULD nonetheless follow the checks-effects-interactions pattern when extending these functions.

**Integer overflow / underflow.** Rate computations multiply before dividing. Implementations MUST use `Math.mulDiv` or equivalent safe-math primitives to prevent precision loss and overflow.

**Authorisation check ordering.** PCEToken validates the registration check (`isExists`) before the ownership check (`owner() == msg.sender`). This ordering is important so that a malicious unregistered contract cannot influence the ownership probe (e.g. by reverting). Implementations MUST keep the registration check first.

### Notes

- **Scope of changes**: Upgrades to both PCEToken and PCECommunityToken are required. Implementation lives behind the existing UUPS proxy / Beacon proxy.
- **PIP-12 status**: Cancelled at the Polygon Timelock layer before any Beacon implementation switch executed. Cancellation tx: `0xe4ed89b0846ce643b29b528e1e2fbe247a51867235ba88cf1760fd944661e1fb` (Polygon, 2026-04-26). PIP-12 SHOULD be marked `Withdrawn` once this proposal is finalised.
- **Out of scope**: Treasury / custody arrangements (multisig, DAO governance, etc.) are at each community's discretion. Community dissolution is left to a separate proposal.
- **Dependencies**: None (built on the existing UUPS / Beacon proxy infrastructure introduced by prior PIPs).
