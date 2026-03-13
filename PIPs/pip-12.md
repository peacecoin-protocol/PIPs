---
pip: 12
title: Community Treasury Wallet and Capital Operations
proposer: chiba (on GitHub)
status: Draft
type: Core
created: 2026-03-13
requires: []
replaces: []
---

## PIP-12: Community Treasury Wallet and Capital Operations

### Abstract

This proposal introduces a treasury wallet mechanism for PCECommunityToken, enabling capital increase and decrease operations that adjust the swap rate (`exchangeRate`) by managing PCE reserves. A new `TREASURY_MANAGER_ROLE` governs access to these operations.

### Motivation

In the current PCECommunityToken design, the swap rate (`exchangeRate`) is fixed at token creation and cannot be changed afterward. This rigidity prevents communities from adjusting token value in response to growth, funding needs, or changing economic conditions.

This proposal enables:
- Community operators to add PCE reserves to increase token value (capital increase)
- Community operators to withdraw PCE reserves for operational funds (capital decrease)
- Swap rate adjustment through PCE reserve management

### Specification

#### Interface

The following interfaces are added to the respective contracts.

**PCECommunityToken — new functions:**

```solidity
function initializeTreasury(address wallet) external;
function setTreasuryWallet(address wallet) external;
function capitalIncrease(uint256 pceAmount) external;
function capitalDecrease(uint256 pceAmount) external;
function hasRole(bytes32 role, address account) external view returns (bool);
function grantTreasuryManagerRole(address account) external;
function revokeTreasuryManagerRole(address account) external;
function treasuryWallet() external view returns (address);
```

**PCEToken — new functions:**

```solidity
function addCapital(address communityToken, uint256 pceAmount, address treasuryWallet) external;
function removeCapital(address communityToken, uint256 pceAmount, address treasuryWallet) external;
```

#### Storage

The following storage variables are added to `PCECommunityToken`:

```solidity
address public treasuryWallet;
bytes32 public constant TREASURY_MANAGER_ROLE = keccak256("TREASURY_MANAGER_ROLE");
mapping(bytes32 => mapping(address => bool)) private _roles;
```

All new variables are appended after existing storage. No existing storage slots are modified.

#### Events

```solidity
event TreasuryWalletSet(address indexed wallet);
event CapitalIncreased(uint256 pceAmount, uint256 oldExchangeRate, uint256 newExchangeRate);
event CapitalDecreased(uint256 pceAmount, uint256 oldExchangeRate, uint256 newExchangeRate);
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

Withdraws PCE reserves from the PCEToken contract to the treasury wallet and adjusts the swap rate upward, decreasing the value of existing community tokens.

1. `pceAmount` MUST be strictly less than `depositedPCEToken` (full withdrawal is not permitted; see Rationale)
2. `pceAmount` PCE is transferred from the PCEToken contract to the treasury wallet
3. `depositedPCEToken` is decreased by `pceAmount`
4. `exchangeRate` is adjusted: `newRate = oldRate * oldDeposited / (oldDeposited - pceAmount)`
5. No community tokens are burned
6. Emits `CapitalDecreased`

#### Symmetry of Capital Operations

| | Capital Increase | Capital Decrease |
|---|---|---|
| PCE flow | Treasury → Contract | Contract → Treasury |
| `depositedPCEToken` | Increases | Decreases |
| `exchangeRate` | Adjusted downward (token value ↑) | Adjusted upward (token value ↓) |
| Token mint/burn | None | None |

#### Role Management

`TREASURY_MANAGER_ROLE` controls access to `capitalIncrease`, `capitalDecrease`, and `setTreasuryWallet`. The role is managed as follows:

- `initializeTreasury(wallet)`: One-time setup. MUST be called by the contract owner. Sets the treasury wallet and grants `TREASURY_MANAGER_ROLE` to `msg.sender`. MUST revert if `treasuryWallet` is already set.
- `grantTreasuryManagerRole(account)`: Grants the role to `account`. MUST be called by an existing treasury manager.
- `revokeTreasuryManagerRole(account)`: Revokes the role from `account`. MUST be called by an existing treasury manager.

### Rationale

**Why adjust exchangeRate instead of minting/burning community tokens?**
Minting would dilute existing holders. Burning would require holders to surrender tokens. Adjusting `exchangeRate` changes the PCE-equivalent value of all community tokens uniformly without affecting token balances or total supply.

**Why a custom role system instead of OpenZeppelin AccessControl?**
PCECommunityToken uses a Beacon proxy pattern. Inheriting AccessControl would introduce new base contract storage slots that conflict with the existing storage layout of deployed proxies. A lightweight mapping-based role system avoids this.

**Why is full withdrawal not permitted?**
If `depositedPCEToken` reaches zero, the exchange rate formula `oldRate * oldDeposited / (oldDeposited - pceAmount)` produces a division by zero. Additionally, resuming from zero via capital increase would produce `oldRate * 0 / (0 + pceAmount) = 0`, corrupting the exchange rate. Community dissolution (full withdrawal of reserves) involves broader considerations and should be addressed in a separate proposal.

### Backwards Compatibility

All changes are additive: new functions, new storage variables (appended after existing ones), and new events. No existing function signatures or behavior are modified.

### Test Cases

All examples use `INITIAL_FACTOR = 10^18`.

**Case 1: Capital Increase**
- Initial state: `depositedPCEToken = 100e18`, `exchangeRate = 1e18`
- Action: `capitalIncrease(50e18)`
- Result: `depositedPCEToken = 150e18`, `exchangeRate = 1e18 * 100e18 / 150e18 = 0.667e18`
- Effect: A user swapping 1 community token now receives more PCE (rate decreased → each community token is worth more PCE)

**Case 2: Capital Decrease**
- Initial state: `depositedPCEToken = 100e18`, `exchangeRate = 1e18`
- Action: `capitalDecrease(25e18)`
- Result: `depositedPCEToken = 75e18`, `exchangeRate = 1e18 * 100e18 / 75e18 = 1.333e18`
- Effect: A user swapping 1 community token now receives less PCE (rate increased → each community token is worth less PCE)

**Case 3: Full withdrawal rejected**
- Initial state: `depositedPCEToken = 100e18`
- Action: `capitalDecrease(100e18)`
- Result: Reverts (full withdrawal is not permitted)

**Case 4: Unauthorized access**
- Action: Non-treasury-manager calls `capitalIncrease(10e18)`
- Result: Reverts

### Security Considerations

**Compromised TREASURY_MANAGER_ROLE**: A malicious treasury manager could withdraw most PCE reserves via repeated `capitalDecrease` calls, severely devaluing the community token. Communities SHOULD use multisig wallets or DAO governance to manage the treasury manager role. However, the governance model is outside the scope of this proposal and is left to each community's discretion.

**Front-running**: A capital decrease transaction in the mempool could be front-run by a user calling `swapFromLocalToken` at the current (more favorable) rate before the rate adjustment takes effect. Mitigation strategies (e.g., commit-reveal, private mempools) are outside the scope of this proposal.

**Reentrancy**: `addCapital` uses `transferFrom` (external call) and `removeCapital` uses `transfer` (external call). Implementations MUST follow the checks-effects-interactions pattern or use a reentrancy guard to prevent reentrancy attacks.

**Integer overflow/underflow**: The exchange rate formula uses multiplication before division. Implementations MUST use `Math.mulDiv` or equivalent safe math to prevent precision loss and overflow.

### Notes

- **Scope of impact**: Requires upgrades to both PCEToken and PCECommunityToken contracts
- **Alternatives considered**: OpenZeppelin AccessControl was considered but rejected due to storage layout conflicts (see Rationale)
- **Out of scope**: Treasury wallet management (e.g., multisig, DAO governance) is left to each community's discretion. Community dissolution (full reserve withdrawal) should be addressed in a separate proposal.
- **Dependencies**: None (builds on the existing UUPS/Beacon proxy architecture)
