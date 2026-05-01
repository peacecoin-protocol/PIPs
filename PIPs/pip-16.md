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

This proposal supersedes PIP-12 with a simplified design. It introduces token value increase and token split operations on PCECommunityToken that adjust the swap rate (`exchangeRate`), gated by `onlyOwner`. The dedicated `RATE_MANAGER_ROLE` and `treasuryWallet` storage proposed in PIP-12 are removed; PCE reserves are pulled from the community owner. Communities that want to govern these operations through a DAO simply transfer ownership to a Governor + Timelock setup, which makes the Timelock both the operation authority and the on-chain treasury for the community.

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

The following interfaces are added to the respective contracts.

**PCECommunityToken — new functions:**

```solidity
function increaseTokenValue(uint256 pceAmount) external;
function splitToken(uint256 mintAmount) external;
```

Both functions are gated by `onlyOwner`. No new role-management functions are introduced.

**PCEToken — new functions:**

```solidity
function addReserve(address communityToken, uint256 pceAmount) external;
```

`addReserve` MUST be called only by the registered PCECommunityToken contract (enforced by `require(msg.sender == communityToken)` and `localTokens[communityToken].isExists`). PCE is pulled from the community owner (the address returned by `OwnableUpgradeable(communityToken).owner()`) via the standing approval that the community owner has granted to the PCEToken contract.

**Note: `addReserve` is not a client-facing entrypoint.** It is an internal coordination function between the two contracts: PCECommunityToken's `increaseTokenValue` calls `addReserve` to atomically update `depositedPCEToken` accounting (which lives on PCEToken), pull PCE from the community owner (using PCEToken's own ERC20 internals), and adjust `exchangeRate`. Clients (community owners, multisigs, Timelocks, etc.) interact only with `PCECommunityToken.increaseTokenValue` and `PCECommunityToken.splitToken`. A direct call to `addReserve` from any address other than the registered PCECommunityToken contract reverts. This pattern follows the same convention as the existing `PCECommunityToken.mint` / `PCECommunityToken.recordSwapToPCE` functions, which are similarly gated to only accept calls from PCEToken.

#### Storage

The following storage variables are added to `PCECommunityToken`:

```solidity
uint256 public rebaseFactor; // initialized lazily; treated as INITIAL_FACTOR (1e18) when zero
```

All new variables MUST be appended to the end of existing storage. Existing storage slots MUST NOT be modified.

The PIP-12-only variables (`treasuryWallet`, `_roles`, role-related constants) are NOT introduced. PIP-12's PR was cancelled before any Beacon implementation switch took effect, so no compatibility shim is required.

#### Events

```solidity
event TokenValueIncreased(uint256 pceAmount, uint256 oldExchangeRate, uint256 newExchangeRate);
event TokenSplit(uint256 mintAmount, uint256 oldExchangeRate, uint256 newExchangeRate, uint256 oldRebaseFactor, uint256 newRebaseFactor);
```

The PIP-12 events `TreasuryWalletSet`, `RateManagerRoleGranted`, and `RateManagerRoleRevoked` are removed.

#### Token Value Increase

Transfers PCE from the community owner to the PCEToken contract and adjusts the swap rate downward, increasing the per-token PCE value of every existing holder.

1. The community owner MUST have approved at least `pceAmount` of PCE to the PCEToken contract in advance.
2. Transfer `pceAmount` of PCE from the community owner to the PCEToken contract via the standing approval.
3. Increase `depositedPCEToken` by `pceAmount`.
4. Adjust `exchangeRate`: `newRate = oldRate * oldDeposited / (oldDeposited + pceAmount)`.
5. No community tokens are minted.
6. Emit `TokenValueIncreased`.

#### Token Split

Adjusts the rebase factor to mint new community tokens and distributes them proportionally to all existing holders. PCE reserves are NOT withdrawn. The swap rate is adjusted upward to reflect the increased token supply, decreasing the per-token PCE value.

1. `mintAmount` is specified in the display (post-rebase) unit of the community token.
2. Adjust `rebaseFactor`: `newRebaseFactor = oldRebaseFactor * (totalDisplaySupply + mintAmount) / totalDisplaySupply`.
3. All holders' display balances increase proportionally: `newDisplayBalance = oldDisplayBalance * newRebaseFactor / oldRebaseFactor`.
4. Adjust `exchangeRate`: `newRate = oldRate * newRebaseFactor / oldRebaseFactor`.
5. `depositedPCEToken` is unchanged.
6. No PCE moves.
7. Emit `TokenSplit`.

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

Both operations are gated by `onlyOwner`. There is no separate role:

- For self-managed communities, the community owner is the address that called `createToken` (typically an EOA or multisig); the community owner calls these functions directly.
- For DAO-managed communities, the community deploys a Governor + TimelockController and calls `transferOwnership(timelock)`. The community's Timelock then becomes the operation authority and the on-chain treasury that holds PCE for `increaseTokenValue`. Proposals are batched: `approve(PCEToken, amount)` from the Timelock + `increaseTokenValue(amount)` in the same batch.
- Migration between the two modes is performed via standard `transferOwnership`. No PIP-specific migration step is required.

### Rationale

**Why `onlyOwner` instead of a dedicated role?**
The community owner of PCECommunityToken is already community-scoped (assigned at `createToken` to the creator). Communities that need governance simply set the community owner to a Governor's Timelock; communities that operate informally keep the community owner on a multisig or EOA. A separate `RATE_MANAGER_ROLE` is only beneficial if the community owner and the rate-changing authority are intended to live at different addresses, which has no concrete operational use case. Aligning all governable operations (`setTokenSettings`, `increaseTokenValue`, `splitToken`) under the same authority keeps the mental model and the DAO proposal flow coherent.

**Why no `treasuryWallet`?**
The PCE used for token value increases is held wherever the community's PCE custody lives. When the community owner is a DAO Timelock, that Timelock is also the caller of `increaseTokenValue`, so the community owner is the correct and only address to pull PCE from. A separate `treasuryWallet` would only matter if the rate-changing authority and the PCE custodian were intentionally different addresses; in practice they are not. Removing the slot also removes the management surface (`initializeTreasury`, `setTreasuryWallet`).

**Why use a factor-based rebase rather than iterating holders?**
Iterating all holders on-chain is gas-prohibitive and would require maintaining an enumerable holder set. A factor-based approach achieves O(1) gas regardless of holder count and is consistent with PCECommunityToken's existing demurrage factor pattern.

**Why adjust `exchangeRate` for token value increases?**
Minting would dilute existing holders. Adjusting `exchangeRate` changes the per-token PCE value uniformly across all community tokens without touching balances or total supply.

**Why use rebase distribution rather than PCE withdrawal for token splits?**
PCE withdrawal to a treasury would deplete the reserves backing the community token. Rebase keeps PCE reserves intact and instead expands token supply proportionally. No value leaves the system; the economic effect is uniform supply expansion (analogous to a stock split), preserving each holder's ownership share while adjusting the per-token value.

**Comparison to PIP-12.**
| | PIP-12 | PIP-16 (this) |
|---|---|---|
| Authority for value ops | dedicated `RATE_MANAGER_ROLE` | `onlyOwner` |
| PCE source | `treasuryWallet` (separate slot) | community owner |
| Storage added (`PCECommunityToken`) | `treasuryWallet`, `_roles`, `RATE_MANAGER_ROLE`, `rebaseFactor` | `rebaseFactor` only |
| Admin functions added | `initializeTreasury`, `setTreasuryWallet`, `grantRateManagerRole`, `revokeRateManagerRole`, `hasRole` | none |
| Events added | 5 | 2 |
| Migration path to DAO governance | grant role + set treasury | `transferOwnership` (already supported) |
| Bytecode impact | larger (relevant given Polygon 32 KB ceiling) | minimal |

### Backwards Compatibility

All changes are additive: new functions, one new storage variable (`rebaseFactor`) appended to existing storage, two new events. Existing function signatures and behaviour are unchanged.

PIP-12 was cancelled on the Polygon Timelock before its Beacon implementation switch executed, so its proposed storage layout (`treasuryWallet`, `_roles`) was never committed on Production. PIP-16 therefore starts from the current production storage layout with no conflicts.

`rebaseFactor` is treated as `INITIAL_FACTOR` (1e18) when zero, so PCECommunityTokens that have never been split continue to compute display balances identically to before the upgrade.

### Test Cases

All examples use `INITIAL_FACTOR = 10^18`.

**Case 1: Token value increase**
- Initial: `depositedPCEToken = 100e18`, `exchangeRate = 1e18`
- Community owner has approved 50e18 PCE to PCEToken
- Operation: `increaseTokenValue(50e18)`
- Result: `depositedPCEToken = 150e18`, `exchangeRate = 1e18 * 100e18 / 150e18 ≈ 0.667e18`
- Effect: Per-token PCE value of every holder increases.

**Case 2: Token split (supply expansion)**
- Initial: `totalDisplaySupply = 200e18`, `exchangeRate = 1e18`, `rebaseFactor = 1e18`
- Operation: `splitToken(50e18)`
- Result: `rebaseFactor = 1.25e18`, `exchangeRate = 1.25e18`
- Effect: All holders' display balances increase by 25 %. Per-token PCE value decreases. `depositedPCEToken` is unchanged. Each holder's PCE-equivalent total is unchanged.

**Case 3: Token split preserves ownership shares**
- Initial: Alice 100 (50 %), Bob 100 (50 %), `totalDisplaySupply = 200e18`
- Operation: `splitToken(100e18)`
- Result: Alice 150 (50 %), Bob 150 (50 %), `totalDisplaySupply = 300e18`

**Case 4: Unauthorised access**
- A non-owner account calls `increaseTokenValue(10e18)`
- Result: revert (`Ownable: caller is not the owner`)

**Case 5: Insufficient approval**
- Community owner has approved 0 PCE to PCEToken
- Operation: `increaseTokenValue(10e18)`
- Result: revert (`ERC20InsufficientAllowance` from PCEToken)

**Case 6: DAO governance flow**
- Community owner of the PCECommunityToken is a TimelockController.
- A Governor proposal batches: (a) `PCEToken.approve(PCEToken, amount)` from the Timelock, (b) `communityToken.increaseTokenValue(amount)`.
- Both calls execute under the Timelock as the community owner.
- Result: PCE leaves the Timelock (the community's on-chain treasury) and is added to the reserves; `exchangeRate` adjusts.

### Security Considerations

**Compromise of the community owner.** A compromised community owner can call `splitToken` repeatedly to dilute the per-token PCE value, or can drain its own approved PCE via `increaseTokenValue`. PCE never leaves the system as a result of `splitToken`, so the attack surface for value-extraction is bounded by the community owner's own PCE balance and approval. Communities SHOULD use a multisig or DAO Governor + Timelock for the community owner role. The governance model is out of scope for this proposal.

**Front-running.** A user MAY attempt to front-run a pending `splitToken` transaction with a `swapFromLocalToken` at the pre-split rate. Because no PCE leaves reserves on a split, the impact is limited to rate arbitrage. Mitigation strategies (commit-reveal, private mempools, etc.) are out of scope.

**Reentrancy.** `increaseTokenValue` performs an external call (the internal pull from the community owner). `splitToken` is purely internal (factor adjustment, no external calls). Implementations MUST either follow the checks-effects-interactions pattern or use a reentrancy guard for `increaseTokenValue`.

**Integer overflow / underflow.** Rate computations multiply before dividing. Implementations MUST use `Math.mulDiv` or equivalent safe-math primitives to prevent precision loss and overflow.

**Approval grant requirement.** `increaseTokenValue` relies on a prior `approve` from the community owner. In the DAO / Timelock flow this is provided by including the `approve` call in the same proposal batch as `increaseTokenValue`, which guarantees atomicity within the batch executor.

### Notes

- **Scope of changes**: Upgrades to both PCEToken and PCECommunityToken are required. Implementation lives behind the existing UUPS proxy / Beacon proxy.
- **PIP-12 status**: Cancelled at the Polygon Timelock layer before any Beacon implementation switch executed. Cancellation tx: `0xe4ed89b0846ce643b29b528e1e2fbe247a51867235ba88cf1760fd944661e1fb` (Polygon, 2026-04-26). PIP-12 SHOULD be marked `Withdrawn` once this proposal is finalised.
- **Out of scope**: Treasury / custody arrangements (multisig, DAO governance, etc.) are at each community's discretion. Community dissolution is left to a separate proposal.
- **Dependencies**: None (built on the existing UUPS / Beacon proxy infrastructure introduced by prior PIPs).
