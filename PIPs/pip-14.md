---
pip: 14
title: Transfer Contract Ownership to Governance-Controlled Timelock
proposer: CHIBA Masahiro (on GitHub)
status: Draft
type: Core
created: 2026-03-20
requires: []
replaces: []
---

## PIP-14: Transfer Contract Ownership to Governance-Controlled Timelock

### Abstract

This proposal transfers the `owner` of PCEToken and the UpgradeableBeacon for PCECommunityToken from an externally owned account (EOA) to a Polygon TimelockController that is governed by the Ethereum PCE Governor via Wormhole cross-chain messaging. After this transfer, all privileged operations — including minting, parameter changes, and contract upgrades — MUST pass through an on-chain governance vote on Ethereum, a cross-chain relay, and a 1-day timelock delay on Polygon before execution.

### Motivation

The current ownership of PCEToken and PCECommunityToken's beacon on Polygon is held by a single EOA (`0xB3FF2e1aBBb6194ff2D47047642981F12D37610A`). This creates two problems:

1. **Single point of failure**: If the private key is compromised or lost, an attacker could mint unlimited tokens, change swap parameters, or upgrade contracts to malicious implementations. Alternatively, key loss would make the contracts permanently un-upgradeable.

2. **Trust assumption**: Token holders must trust the EOA holder to act in the community's interest. There is no on-chain mechanism for the community to approve or reject changes before they take effect.

The cross-chain governance infrastructure (GovernanceSender on Ethereum, GovernanceReceiver + TimelockController on Polygon) has been deployed as part of the multichain governance plan (Phase 2). This proposal activates that infrastructure by transferring ownership to the Polygon TimelockController, completing the transition from EOA-controlled to governance-controlled operations.

### Specification

#### Contracts Affected

| Contract | Chain | Address | Current Owner |
|----------|-------|---------|---------------|
| PCEToken (DEV proxy) | Polygon | `0x62Ef93EAa5bB3E47E0e855C323ef156c8E3D8913` | EOA `0xB3FF...610A` |
| PCECommunityToken beacon (DEV) | Polygon | `0xA9D965660dcF0fA73E709fd802e9DEF2d9b52952` | EOA `0xB3FF...610A` |

Production contracts (`0xA4807a...` and `0x6A73A6...`) follow the same procedure separately once DEV validation is complete.

#### Ownership Transfer Procedure

The current EOA owner MUST execute the following transactions on Polygon:

**Step 1: Transfer PCEToken ownership**

```solidity
PCEToken(0x62Ef93EAa5bB3E47E0e855C323ef156c8E3D8913)
    .transferOwnership(POLYGON_TIMELOCK_ADDRESS);
```

**Step 2: Transfer UpgradeableBeacon ownership**

```solidity
UpgradeableBeacon(0xA9D965660dcF0fA73E709fd802e9DEF2d9b52952)
    .transferOwnership(POLYGON_TIMELOCK_ADDRESS);
```

Both calls use OpenZeppelin's `OwnableUpgradeable.transferOwnership()` / `Ownable.transferOwnership()` which performs an immediate transfer (single-step).

#### Post-Transfer Privilege Model

After transfer, the following functions become accessible only through the governance path:

**PCEToken (`onlyOwner`):**

| Function | Description |
|----------|-------------|
| `mint(address, uint256)` | Mint new PCE tokens |
| `setCommunityTokenAddress(address)` | Set community token implementation |
| `setNativeTokenToPceTokenRate(uint160)` | Set native-to-PCE exchange rate |
| `setMetaTransactionGas(uint256)` | Set meta-transaction gas limit |
| `setMetaTransactionPriorityFee(uint256)` | Set meta-transaction priority fee |
| `_authorizeUpgrade(address)` | Authorize UUPS proxy upgrade |

**PCECommunityToken beacon (`onlyOwner`):**

| Function | Description |
|----------|-------------|
| `upgradeTo(address)` | Upgrade all community token implementations |

#### Governance Execution Path

After ownership transfer, all privileged operations follow this path:

```
1. Proposal created on Ethereum Governor (requires 1,000 WPCE)
2. Voting period on Ethereum (~3 days)
3. If approved (quorum: 500,000 WPCE), queued in Ethereum Timelock (2 days)
4. Ethereum Timelock executes → GovernanceSender → Wormhole Relayer
5. GovernanceReceiver on Polygon receives and schedules on Polygon Timelock (1 day)
6. Anyone executes the Polygon Timelock operation
```

Total minimum delay from proposal to execution: ~6 days (3d vote + 2d Ethereum Timelock + 1d Polygon Timelock).

### Rationale

**Why single-step transfer instead of two-step?**

OpenZeppelin's `OwnableUpgradeable` uses single-step `transferOwnership()`. While a two-step transfer (with `acceptOwnership()`) is safer against mistyped addresses, the Polygon Timelock is a well-known, verified contract deployed in an earlier step. The risk of address error is mitigated by verification on a testnet deployment first (DEV environment).

**Why transfer the beacon separately?**

PCECommunityToken uses the Beacon Proxy pattern. The beacon's owner controls upgrades for all community token instances. Transferring the beacon's owner to the Timelock ensures that community token upgrades also go through governance, not just PCEToken upgrades.

**Why a 1-day delay on Polygon Timelock?**

The Ethereum side already enforces a 2-day Timelock delay. The additional 1-day delay on Polygon serves as a last-resort observation window: if a malicious proposal somehow passes governance, community members have 1 day to detect and react after the cross-chain message arrives but before execution.

**Why not use `Ownable2Step`?**

Migrating PCEToken to `Ownable2StepUpgradeable` would require a contract upgrade, which is a separate scope. This proposal uses the existing `transferOwnership()` interface.

### Backwards Compatibility

This proposal introduces no changes to contract interfaces. All existing functions continue to work identically. The only behavioral change is that `onlyOwner` functions revert when called by the former EOA owner, since `owner()` now returns the Polygon Timelock address.

Community tokens created via `PCEToken.createToken()` are unaffected — they are deployed as BeaconProxy instances and do not have individual owners.

### Test Cases

**TC-1: Ownership transfer succeeds**

```
Pre-condition: PCEToken.owner() == EOA
Action: EOA calls PCEToken.transferOwnership(timelockAddress)
Post-condition: PCEToken.owner() == timelockAddress
```

**TC-2: Former owner cannot call onlyOwner functions**

```
Pre-condition: PCEToken.owner() == timelockAddress
Action: EOA calls PCEToken.mint(alice, 1000e18)
Expected: Revert with OwnableUnauthorizedAccount(EOA)
```

**TC-3: Governance-approved mint executes successfully**

```
Pre-condition: PCEToken.owner() == timelockAddress
Action:
  1. Ethereum Governor proposal to mint(alice, 1000e18) passes vote
  2. Queued in Ethereum Timelock (2 days)
  3. Executed → GovernanceSender → Wormhole → GovernanceReceiver
  4. Scheduled on Polygon Timelock (1 day)
  5. Executed on Polygon Timelock
Post-condition: PCEToken.balanceOf(alice) increases by 1000e18
```

**TC-4: Governance-approved upgrade executes successfully**

```
Pre-condition: UpgradeableBeacon.owner() == timelockAddress
Action: Governance proposal to beacon.upgradeTo(newImpl) passes full path
Post-condition: beacon.implementation() == newImpl
```

### Security Considerations

**Key compromise of former EOA**: After ownership transfer, the EOA private key no longer has any privileged access. Even if compromised, the attacker cannot affect the contracts. The EOA SHOULD be considered a non-sensitive key after transfer.

**Wormhole relay failure**: If Wormhole fails to deliver a message, the governance operation stalls but does not cause harm. The operation can be re-submitted through a new governance proposal.

**Governance attack (51% vote)**: An attacker accumulating >500,000 WPCE could pass malicious proposals. The 2-day Ethereum Timelock + 1-day Polygon Timelock provide a combined 3-day window for the community to detect and respond. The GovernanceReceiver owner (Polygon Timelock) can cancel scheduled operations via `cancelScheduledBatch()`.

**Irrecoverability**: Ownership transfer is irreversible by the EOA. If the governance system (Governor, Wormhole, or Timelock) has a critical bug, recovery requires a governance proposal through that same system. This risk is mitigated by the pre-existing deployment and testing of the governance infrastructure on the DEV environment.

**Transfer ordering**: Both transfers (PCEToken and beacon) SHOULD be executed in the same transaction batch or in immediate succession to minimize the window where one contract is governance-controlled and the other is EOA-controlled.

### Notes

**Scope**: This proposal covers only ownership transfer. It does not modify any contract code, storage layout, or function behavior.

**DEV-first rollout**: The transfer MUST be performed on the DEV environment first. Production transfer follows after successful validation of the full governance flow (propose → vote → execute → cross-chain relay → Polygon execution).

**Out of scope**:
- Migration to `Ownable2StepUpgradeable` (may be proposed separately)
- Governance parameter changes (voting period, quorum, proposal threshold)
- Multi-chain expansion beyond Ethereum↔Polygon
- Phase 3 contract migration (separate PIP)
