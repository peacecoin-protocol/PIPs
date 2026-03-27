---
pip: 12
title: Community Treasury Wallet and Capital Operations
proposer: CHIBA Masahiro (@nihen)
status: Draft
type: Core
created: 2026-03-13
requires: []
replaces: []
---

## PIP-12: Community Treasury Wallet and Capital Operations

### Abstract

This proposal introduces a treasury wallet mechanism for PCECommunityToken, enabling capital increase and decrease operations that adjust the swap rate (`exchangeRate`). Capital increase adds PCE reserves from the treasury wallet. Capital decrease mints new community tokens and distributes them proportionally to all existing holders via a rebase factor, without withdrawing PCE from reserves. A new `TREASURY_MANAGER_ROLE` governs access to these operations.

### Motivation

In the current PCECommunityToken design, the swap rate (`exchangeRate`) is fixed at token creation and cannot be changed afterward. This rigidity prevents communities from adjusting token value in response to growth, funding needs, or changing economic conditions.

This proposal enables:
- Community operators to add PCE reserves to increase token value (capital increase)
- Community operators to expand token supply via proportional distribution to all holders (capital decrease)
- Swap rate adjustment through reserve management and supply expansion

### Specification

#### Interface

The following interfaces are added to the respective contracts.

**PCECommunityToken — new functions:**

```solidity
function initializeTreasury(address wallet) external;
function setTreasuryWallet(address wallet) external;
function capitalIncrease(uint256 pceAmount) external;
function capitalDecrease(uint256 mintAmount) external;
function hasRole(bytes32 role, address account) external view returns (bool);
function grantTreasuryManagerRole(address account) external;
function revokeTreasuryManagerRole(address account) external;
function treasuryWallet() external view returns (address);
```

**PCEToken — new function:**

```solidity
function addCapital(address communityToken, uint256 pceAmount, address treasuryWallet) external;
```

#### Storage

The following storage variables are added to `PCECommunityToken`:

```solidity
address public treasuryWallet;
bytes32 public constant TREASURY_MANAGER_ROLE = keccak256("TREASURY_MANAGER_ROLE");
mapping(bytes32 => mapping(address => bool)) private _roles;
uint256 public rebaseFactor; // initialized to INITIAL_FACTOR (1e18)
```

All new variables are appended after existing storage. No existing storage slots are modified.

#### Events

```solidity
event TreasuryWalletSet(address indexed wallet);
event CapitalIncreased(uint256 pceAmount, uint256 oldExchangeRate, uint256 newExchangeRate);
event CapitalDecreased(uint256 mintAmount, uint256 oldExchangeRate, uint256 newExchangeRate, uint256 oldRebaseFactor, uint256 newRebaseFactor);
event TreasuryManagerRoleGranted(address indexed account);
event TreasuryManagerRoleRevoked(address indexed account);
```

#### Capital Increase

Transfers PCE from the treasury wallet to the PCEToken contract and adjusts the swap rate downward, increasing the value of existing community tokens.

1. The treasury wallet MUST have approved the PCEToken contract for at least `pceAmount` prior to calling
2. `pceAmount` PCE is transferred from the treasury wallet to the PCEToken contract via `transferFrom`
3. `depositedPCEToken` is increased by `pceAmount`
4. `exchangeRate` is adjusted: `newRate = oldRate * oldDeposited / (oldDeposited + pceAmount)`
5. No community tokens are minted
6. Emits `CapitalIncreased`

#### Capital Decrease

Mints new community tokens and distributes them proportionally to all existing holders by adjusting a rebase factor. No PCE is withdrawn from reserves. The swap rate is adjusted upward to reflect the increased token supply, decreasing the PCE-equivalent value per token.

1. `mintAmount` is specified in community token display units
2. `rebaseFactor` is adjusted: `newRebaseFactor = oldRebaseFactor * (totalDisplaySupply + mintAmount) / totalDisplaySupply`
3. All holders' display balances increase proportionally: `newDisplayBalance = oldDisplayBalance * newRebaseFactor / oldRebaseFactor`
4. `exchangeRate` is adjusted: `newRate = oldRate * newRebaseFactor / oldRebaseFactor`
5. `depositedPCEToken` is unchanged
6. No PCE moves
7. Emits `CapitalDecreased`

The display balance calculation incorporates the rebase factor: `displayBalance = rawBalance * INITIAL_FACTOR / getCurrentFactor() * rebaseFactor / INITIAL_FACTOR`

#### Symmetry of Capital Operations

| | Capital Increase | Capital Decrease |
|---|---|---|
| PCE flow | Treasury → Contract | None |
| `depositedPCEToken` | Increases | Unchanged |
| `exchangeRate` | Adjusted downward (token value ↑) | Adjusted upward (token value ↓) |
| Token supply | Unchanged | Increases (via rebase factor) |
| `rebaseFactor` | Unchanged | Increases |
| Holder balances | Unchanged | All increase proportionally |

#### Role Management

`TREASURY_MANAGER_ROLE` controls access to `capitalIncrease`, `capitalDecrease`, and `setTreasuryWallet`. The role is managed as follows:

- `initializeTreasury(wallet)`: One-time setup. MUST be called by the contract owner. Sets the treasury wallet and grants `TREASURY_MANAGER_ROLE` to `msg.sender`. MUST revert if `treasuryWallet` is already set.
- `grantTreasuryManagerRole(account)`: Grants the role to `account`. MUST be called by an existing treasury manager.
- `revokeTreasuryManagerRole(account)`: Revokes the role from `account`. MUST be called by an existing treasury manager.

### Rationale

**Why adjust exchangeRate for capital increase instead of minting community tokens?**
Minting would dilute existing holders. Adjusting `exchangeRate` changes the PCE-equivalent value of all community tokens uniformly without affecting token balances or total supply.

**Why use rebase-based distribution for capital decrease instead of withdrawing PCE?**
Withdrawing PCE to a treasury wallet depletes the reserves backing the community token. The rebase approach keeps PCE reserves intact and instead expands the token supply proportionally. This ensures no value leaves the system — the economic effect is a uniform supply expansion (analogous to a stock split), preserving each holder's proportional share while adjusting the per-token value.

**Why a custom role system instead of OpenZeppelin AccessControl?**
PCECommunityToken uses a Beacon proxy pattern. Inheriting AccessControl would introduce new base contract storage slots that conflict with the existing storage layout of deployed proxies. A lightweight mapping-based role system avoids this.

**Why use a factor-based rebase instead of iterating holders?**
On-chain iteration over all token holders is prohibitively gas-expensive and requires maintaining an enumerable holder set. A factor-based approach achieves O(1) gas cost regardless of holder count, consistent with the existing demurrage factor pattern in PCECommunityToken.

### Backwards Compatibility

All changes are additive: new functions, new storage variables (appended after existing ones), and new events. No existing function signatures or behavior are modified.

### Test Cases

All examples use `INITIAL_FACTOR = 10^18`.

**Case 1: Capital Increase**
- Initial state: `depositedPCEToken = 100e18`, `exchangeRate = 1e18`
- Action: `capitalIncrease(50e18)`
- Result: `depositedPCEToken = 150e18`, `exchangeRate = 1e18 * 100e18 / 150e18 = 0.667e18`
- Effect: A user swapping 1 community token now receives more PCE (rate decreased → each community token is worth more PCE)

**Case 2: Capital Decrease (supply expansion)**
- Initial state: `totalDisplaySupply = 200e18`, `exchangeRate = 1e18`, `rebaseFactor = 1e18`
- Action: `capitalDecrease(50e18)` (mint 50 tokens)
- Result: `rebaseFactor = 1e18 * (200e18 + 50e18) / 200e18 = 1.25e18`, `exchangeRate = 1e18 * 1.25e18 / 1e18 = 1.25e18`
- Effect: All holders' display balances increase by 25%. Each community token is now worth less PCE (rate increased). `depositedPCEToken` unchanged. Each holder's total PCE-equivalent value unchanged.

**Case 3: Capital Decrease preserves proportional ownership**
- Initial state: Alice holds 100 tokens (50%), Bob holds 100 tokens (50%), `totalDisplaySupply = 200e18`
- Action: `capitalDecrease(100e18)` (mint 100 tokens)
- Result: Alice holds 150 tokens (50%), Bob holds 150 tokens (50%), `totalDisplaySupply = 300e18`
- Effect: Both holders' proportional ownership is preserved

**Case 4: Unauthorized access**
- Action: Non-treasury-manager calls `capitalIncrease(10e18)`
- Result: Reverts

### Security Considerations

**Compromised TREASURY_MANAGER_ROLE**: A malicious treasury manager could repeatedly call `capitalDecrease` to inflate the token supply, devaluing each token's PCE-equivalent worth. However, since no PCE leaves the system and all holders are diluted equally, the attack surface is limited to exchange rate manipulation. Communities SHOULD use multisig wallets or DAO governance to manage the treasury manager role. However, the governance model is outside the scope of this proposal and is left to each community's discretion.

**Front-running**: A capital decrease transaction in the mempool could be front-run by a user calling `swapFromLocalToken` at the current (more favorable) rate before the rate adjustment takes effect. Since capital decrease does not remove PCE from reserves, the impact is limited to rate arbitrage. Mitigation strategies (e.g., commit-reveal, private mempools) are outside the scope of this proposal.

**Reentrancy**: `addCapital` uses `transferFrom` (external call). Capital decrease is purely internal (factor adjustment, no external calls). Implementations MUST follow the checks-effects-interactions pattern or use a reentrancy guard on `addCapital` to prevent reentrancy attacks.

**Integer overflow/underflow**: The exchange rate formula uses multiplication before division. Implementations MUST use `Math.mulDiv` or equivalent safe math to prevent precision loss and overflow.

### Notes

- **Scope of impact**: Requires upgrades to both PCEToken and PCECommunityToken contracts
- **Alternatives considered**: OpenZeppelin AccessControl was considered but rejected due to storage layout conflicts (see Rationale)
- **Out of scope**: Treasury wallet management (e.g., multisig, DAO governance) is left to each community's discretion. Community dissolution should be addressed in a separate proposal.
- **Dependencies**: None (builds on the existing UUPS/Beacon proxy architecture)
