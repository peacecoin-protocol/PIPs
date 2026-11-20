---
pip: 23
title: New PCE Token and One-to-One Distribution to Legacy PCE Holders
proposer: CHIBA Masahiro (@nihen)
status: Draft
type: Core
created: 2026-11-16
requires: []
replaces: []
---

## PIP-23: New PCE Token and One-to-One Distribution to Legacy PCE Holders

### Abstract

This proposal deploys a new PCE token on Ethereum that only the DAO can mint or change. In one DAO proposal, legacy PCE holders receive the same amount of new PCE, and the DAO's voting token is switched to the new PCE.

### Motivation

1. **The legacy PCE cannot be upgraded.** It is a plain, non-proxy ERC-20. Features cannot be added and defects cannot be fixed.
2. **Minting of the legacy PCE is not under DAO control.** Its `MINTER_ROLE` and `DEFAULT_ADMIN_ROLE` are held by one externally owned account (`0xBeB4C556E8f24aF598f9BDc3f9B5BADB3F08aeEf`).
3. **Voting uses a separate token.** DAO voting uses Wrapped PCE (WPCE, `0xeB5e0632eD3C635E0fa07420A328b49a7D0E6e6d`), which wraps the legacy PCE, so holders have to wrap PCE and handle two tokens to vote. This proposal makes the new PCE itself the voting token.

### Specification

The key words "MUST", "MUST NOT", "SHOULD" and "MAY" are to be interpreted as described in RFC 2119.

#### 1. New PCE token

- ERC-20 with EIP-2612 `permit` and ERC-5805 votes. Name `PEACE COIN`, symbol `PCE`, decimals `18`, EIP-712 domain name `PEACE COIN`, version `1`.
- Base contracts (OpenZeppelin Contracts Upgradeable v5): `ERC20Upgradeable`, `ERC20PermitUpgradeable`, `ERC20VotesUpgradeable`, `OwnableUpgradeable`, `UUPSUpgradeable`, behind an ERC-1967 proxy. `_authorizeUpgrade` MUST be restricted to the owner. The implementation constructor MUST call `_disableInitializers()`.
- Owner: the Ethereum Timelock (`0x208983f723245C765b4F9E57FAaB2633c9DDaC6B`), set in the deployment transaction. The deployer MUST NOT hold any role at any time. `renounceOwnership()` MUST revert with `RenounceOwnershipDisabled()`.
- `clock()` MUST return `uint48(block.number)` and `CLOCK_MODE()` MUST return `mode=blocknumber&from=default`, because the Governor counts its periods in blocks.
- There is no automatic delegation. Balances count as votes only after the holder delegates. Holders MAY delegate before the distribution.
- Token-specific state MUST use ERC-7201 namespaced storage:

```solidity
/// @custom:storage-location erc7201:peacecoin.storage.PCEToken
struct PCETokenStorage {
    bool distributionClosed;
}
// keccak256(abi.encode(uint256(keccak256("peacecoin.storage.PCEToken")) - 1)) & ~bytes32(uint256(0xff))
bytes32 constant PCE_TOKEN_STORAGE_LOCATION = 0x3629129d1ffd87df7c2b731c970b9f725273df030c447616868b5704115a1a00;
```

```solidity
interface IPCEToken {
    /// @notice Called once through the proxy constructor. `initialOwner` MUST be the Ethereum Timelock.
    function initialize(address initialOwner) external;

    /// @notice Mints the given amounts to the given recipients. onlyOwner.
    function distribute(address[] calldata recipients, uint256[] calldata amounts) external;

    /// @notice Permanently disables `distribute`. onlyOwner.
    function closeDistribution() external;

    function distributionClosed() external view returns (bool);

    event Distributed(address indexed recipient, uint256 amount);
    event DistributionClosed(uint256 totalSupply);
}
```

- `distribute` MUST revert with `DistributionIsClosed()` when closed, `EmptyDistribution()` when `recipients` is empty, `LengthMismatch()` when the array lengths differ, `ZeroRecipient()` for the zero address, and `ZeroAmount()` for a zero amount. It MUST emit `Distributed(recipient, amount)` after each mint. It does not check for duplicates; the table MUST NOT contain any.
- `closeDistribution` MUST revert with `DistributionIsClosed()` when already closed; otherwise it sets `distributionClosed` and emits `DistributionClosed(totalSupply())`.
- `distribute` is the only minting path.

#### 2. Governor upgrade

The Ethereum Governor (proxy `0x00831a36ce3535effefe54bad0bb8de27687a237`, implementation `0x911c1f527651374ba6546b1f1510832084af5208`) is upgraded to `PCEGovernorV2`, which extends the current `PCEGovernor`. It MUST keep the storage layout and pass the OpenZeppelin upgrades validator against the current implementation. Its constructor MUST call `_disableInitializers()`.

```solidity
/// @notice Switches the token whose votes the Governor counts.
/// @dev Only the Timelock (`_executor()`) may call it. reinitializer(2). Called through upgradeToAndCall.
function switchVotingToken(IVotes newToken) external;

event VotingTokenSwitched(address indexed oldToken, address indexed newToken);

error InvalidVotingToken();
```

`switchVotingToken(newToken)` MUST:

1. revert with `GovernorOnlyExecutor` unless `msg.sender` is `_executor()`. It MUST NOT use `onlyGovernance`, which fails inside `upgradeToAndCall` because the Governor's governance-call queue holds the outer call, not this one;
2. revert with `InvalidVotingToken()` if `newToken` is the zero address or the current `token()`, or its `CLOCK_MODE()` differs from the current one;
3. call `__GovernorVotes_init_unchained(newToken)`;
4. emit `VotingTokenSwitched(oldToken, newToken)`.

The voting rules are unchanged: `votingDelay` 7,200 blocks, `votingPeriod` 21,600 blocks, `proposalThreshold` 1,000 votes, quorum 500,000 votes (For plus Abstain), simple majority. `_quorumSupplyToken` is not used by `quorum()` and is left unchanged. The Governor's initialized version is 1, so `reinitializer(2)` can run once.

#### 3. Snapshot

- Block 25904711 (hash `0x6dd75f8317957156f05c731d9ffee046d23b14813dc55ffc3e4dd7c065c41b74`), the last block before 2026-09-04 15:00:00 UTC. Balances at the end of this block are used.
- Method: the candidate addresses are all addresses in the `Transfer` events of the token up to this block. The balance of each is `balanceOf` at this block, read from an archive node. The balances MUST equal the replay of the `Transfer` events, and their sum MUST equal `totalSupply()` at this block.
- Legacy PCE (events since block 10219538): 134 holders, 222,156,723.49 PCE.
- WPCE (events since block 23302929): 10 holders, 950,000 WPCE, equal to the legacy PCE held by WPCE.

#### 4. Distribution rules

1. **General rule.** Each holder receives new PCE equal to its legacy PCE balance at the snapshot.
2. **Uniswap V2 pairs.** A legacy pair cannot use new PCE, so the pairs `0x56a62dd43f135bad525df9179b0ef4fad5639ca1` (PCE/WETH) and `0x3ce03c46a5de51e7ea690eec54c0eaf0d8028323` (PCE/USDT) receive nothing. Each liquidity provider receives `floor(pairPCE × lpBalance / lpTotalSupply)` by its LP-token balance at the snapshot: `0x659ae334442e2a5425e0b811b144bc507b05a6ee` receives 13,809.267634484359053979 PCE (PCE/WETH) and `0x2b146aadb84fae7fb8426ac52a7d2824a02acafb` receives 4,582.182396909177743467 PCE (PCE/USDT). The minimum liquidity locked at the zero address (about 0.0000000092 PCE in total) and the rounding remainder (1 wei per pair) are not distributed.
3. **Wrapped PCE.** The WPCE contract receives nothing. Each WPCE holder receives new PCE equal to its WPCE balance at the snapshot, in addition to its own balance: 950,000 PCE in total to 10 holders. WPCE itself is not changed and stays backed by the legacy PCE, so unwrapping later returns legacy PCE and nothing is distributed twice.
4. **Custodial exchange address.** `0x822998c27fdab4afb3a5bff4e7e3654349c79d00` (14,008,383.887596769165043070 PCE) receives the same amount. The custodian has agreed to credit its users by their balances at the snapshot.

Result: 138 recipients, 222,156,723.489999990835174897 PCE in total. Only the locked minimum liquidity and the rounding remainder (0.000000009164825103 PCE) are not distributed.

#### 5. Data and reproducibility

Before the DAO proposal is created, the following MUST be published, with SHA-256 hashes of every file:

- the saved `Transfer` logs of the legacy PCE, WPCE and both Uniswap V2 pairs;
- a script that fetches those logs and checks the balances against `balanceOf` and `totalSupply()` at the snapshot block;
- a script that rebuilds the snapshot, the LP and WPCE allocations and the distribution table from the logs without network access;
- the resulting CSV files, sorted by amount (descending), then address (ascending).

[TBD: repository link and final hashes.]

#### 6. Prerequisites before the DAO proposal

The following MUST hold when the DAO proposal is created. Voters SHOULD check them.

1. The new PCE proxy and implementation are deployed and verified on Etherscan. The proxy is OpenZeppelin `ERC1967Proxy`, its admin slot is zero, it emitted exactly one `Upgraded` event, and its implementation slot points to the verified implementation.
2. `newPCE.owner()` is the Timelock, `totalSupply()` is `0`, and `distributionClosed()` is `false`.
3. `PCEGovernorV2` is deployed and verified on Etherscan.
4. The legacy PCE `MINTER_ROLE` and `DEFAULT_ADMIN_ROLE` have been renounced, and `getRoleMemberCount` returns `0` for both.
5. On the Timelock, `PROPOSER_ROLE` is held only by the Governor and the zero address, and `DEFAULT_ADMIN_ROLE` only by the Timelock itself, as confirmed from all `RoleGranted` and `RoleRevoked` events. The claim that every mint needs a passed DAO proposal depends on this.
6. The whole proposal has been executed on a mainnet fork, and its gas is below the per-transaction limit (16,777,216 under EIP-7825).

#### 7. Execution

1. One DAO proposal is created with these actions, in this order:
   1. `newPCE.distribute(recipients, amounts)` with the exact distribution table;
   2. `governor.upgradeToAndCall(pceGovernorV2, abi.encodeCall(PCEGovernorV2.switchVotingToken, (newPCE)))`.

   A failed vote is retried with the same actions and a new description.
2. Both actions run in one transaction through the Timelock's `executeBatch`, so both take effect or neither does. Proposals created afterwards use new PCE votes.
3. No other proposal SHOULD be Pending, Active, Succeeded or Queued at execution. Votes already cast stay counted and passed proposals stay executable, but later votes on an open proposal are weighted by the new PCE at its snapshot block, which is zero before the distribution.
4. `closeDistribution()` is not called by this PIP. Until a later PIP calls it, new PCE can be minted only by a passed DAO proposal.

With the current settings, execution is possible about six days after the proposal is created: about one day before voting, three days of voting and two days of Timelock delay.

### Rationale

- **The Timelock owns the token from deployment.** No individual holds minting or upgrade rights at any time, even briefly.
- **The distribution is the DAO's own action.** Voters approve the exact recipients and amounts that are executed.
- **WPCE holders receive new PCE directly.** Sending new PCE to the WPCE contract would require upgrading WPCE and stopping wrapping and unwrapping until the switch. Distributing to the WPCE holders needs no change to WPCE.
- **Direct distribution in one call.** With 138 recipients, direct distribution is cheap (estimated about 4 million gas, to be replaced by the fork measurement) and avoids claim transactions and unclaimed balances.
- **Liquidity providers instead of pairs.** This follows the token migration documented by Sonic (FTM to S).
- **Minting only through `distribute`.** The DAO keeps the ability to make the additional issuance and decides by vote when to close it. Ongoing issuance tied to the bridge or ARIGATO CREATION is left to later proposals.
- **Voting moves with the token, in the same proposal.** The official PCE and the voting token change together, and no holder can vote with both.
- **No change to the voting rules, and no automatic delegation.** The change is limited to what the migration needs.

### Backwards Compatibility

- The legacy PCE and WPCE are not changed and can still be transferred, wrapped and unwrapped, but after execution neither is the official PCE or the voting token.
- WPCE delegations do not carry over. Every holder, including current WPCE voters, MUST delegate in the new PCE to vote. Until 500,000 votes are delegated, no proposal can reach the quorum.
- The PCE token on Polygon (`0xA4807a8C34353A5EA51aF073175950Cb6248dA7E`) was issued for testing and is not backed by the legacy PCE. It is not changed or counted. A bridge to the new PCE is left to a separate PIP.
- The distribution follows the snapshot. Legacy PCE moved after the snapshot does not change it; support for later holders is left to the additional-issuance PIP.
- Exchanges, wallets, explorers and new liquidity pools need to use the new address. This is outside this PIP.

### Test Cases

- After execution, `totalSupply()` is `222156723489999990835174897`.
- `balanceOf(0x64804de3708478390bd7def274deee66070ec34b)` is `53286622890000000000000000`.
- `balanceOf(0x659ae334442e2a5425e0b811b144bc507b05a6ee)` is `1013629267634484359053979` (own `999820000000000000000000` plus LP share `13809267634484359053979`).
- `balanceOf(0x9052d3da7eebdf4f2b77d1c87fea31c6f789e964)` is `1000000000000000000000000` (own `800000000000000000000000` plus WPCE `200000000000000000000000`).
- `balanceOf` of the WPCE contract, and the Uniswap V2 pairs is `0`.
- Executing the table emits 138 `Distributed` events.
- `distribute` from any account other than the Timelock reverts with `OwnableUnauthorizedAccount`; each invalid input listed in Specification 1 reverts with its error.
- After `closeDistribution()`, `distribute` and a second `closeDistribution()` revert with `DistributionIsClosed()`.
- `renounceOwnership()` reverts with `RenounceOwnershipDisabled()`; `upgradeToAndCall` from any account other than the Timelock reverts; `initialize` cannot be called twice or on the implementation.
- `permit` with a valid signature for (`PEACE COIN`, `1`, chain ID 1, proxy address) sets the allowance.
- A recipient that delegated to itself before the distribution has `getVotes` equal to its table amount after execution; one that did not has `0`.
- After execution, `governor.token()` is the new PCE and the voting rules are unchanged.
- `switchVotingToken` reverts on a second call, from any account other than the Timelock, and with an invalid token.
- On a mainnet fork, after execution, holders with more than 500,000 delegated votes can create, pass, queue and execute a new proposal, and a proposal that was Queued at the switch stays executable.
- The script reproduces the published CSV files byte for byte.

### Security Considerations

- **No cancellation.** No account can cancel on the Ethereum Timelock, so a passed proposal cannot be stopped during the delay.
- **Table integrity.** Voters SHOULD compare the proposal calldata with the published table.
- **Upgrade power.** The Timelock can upgrade the new PCE and the Governor, including adding minting. This is the same trust as the existing Polygon PCE and community tokens.
- **Low participation after the switch.** No votes exist in the new PCE until holders delegate. Holders SHOULD be asked to delegate as soon as the new PCE address is published.
- **Legacy minter.** The legacy roles MUST be renounced before the proposal (Specification 6), so that no account keeps control of the legacy supply.
- **Impersonation.** Fake tokens and claim sites can appear. The official address MUST be announced only through official channels, and announcements SHOULD say that no claim is needed.

### Notes

- **Related PIPs.** A separate PIP defines the additional issuance. A future PIP MAY call `closeDistribution()`.
- **Out of scope.** Bridge to Polygon, issuance tied to ARIGATO CREATION, changes to the voting rules, new liquidity pools, and listings.
- **Approval threshold.** PIP-1 requires a quorum of 20% or more and an approval rate of 66% or more for Core PIPs, but the current Governor enforces only an absolute quorum of 500,000 votes and a simple majority. This is a defect of the current Governor and will be fixed by a separate proposal.
