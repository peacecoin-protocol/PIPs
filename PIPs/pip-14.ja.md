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

## PIP-14: コントラクトの所有権をガバナンス制御の Timelock に移譲

### Abstract（概要）

本提案は、PCEToken および PCECommunityToken の UpgradeableBeacon の `owner` を、現在の外部所有アカウント（EOA）から、Ethereum PCE Governor が Wormhole クロスチェーンメッセージング経由で制御する Polygon TimelockController に移譲する。移譲後、ミント・パラメータ変更・コントラクトアップグレードを含むすべての特権操作は、Ethereum 上のオンチェーンガバナンス投票、クロスチェーンリレー、Polygon 上の1日のタイムロック遅延を経なければ実行できなくなる（MUST）。

### Motivation（動機）

Polygon 上の PCEToken と PCECommunityToken の beacon は、現在単一の EOA（`0xB3FF2e1aBBb6194ff2D47047642981F12D37610A`）が所有している。これにより2つの問題がある：

1. **単一障害点**: 秘密鍵が漏洩した場合、攻撃者は無制限のトークンミント、スワップパラメータの変更、悪意あるコントラクトへのアップグレードが可能になる。逆に鍵を紛失すると、コントラクトは永久にアップグレード不能になる。

2. **信頼前提**: トークン保有者は、EOA 保持者がコミュニティの利益のために行動することを信頼しなければならない。変更が実行される前にコミュニティが承認・拒否するオンチェーンメカニズムが存在しない。

マルチチェーンガバナンス計画（フェーズ2）の一環として、クロスチェーンガバナンスインフラ（Ethereum 上の GovernanceSender、Polygon 上の GovernanceReceiver + TimelockController）の構築が完了した。本提案は、所有権を Polygon TimelockController に移譲することでそのインフラを有効化し、EOA 制御からガバナンス制御への移行を完了する。

### Specification（仕様）

#### 対象コントラクト

| コントラクト | チェーン | アドレス | 現在の owner |
|-------------|---------|---------|-------------|
| PCEToken (DEV proxy) | Polygon | `0x62Ef93EAa5bB3E47E0e855C323ef156c8E3D8913` | EOA `0xB3FF...610A` |
| PCECommunityToken beacon (DEV) | Polygon | `0xA9D965660dcF0fA73E709fd802e9DEF2d9b52952` | EOA `0xB3FF...610A` |

本番コントラクト（`0xA4807a...` および `0x6A73A6...`）は、DEV 環境での検証完了後に同じ手順で別途実施する。

#### 所有権移譲手順

現在の EOA owner が Polygon 上で以下のトランザクションを実行する（MUST）：

**ステップ 1: PCEToken の所有権移譲**

```solidity
PCEToken(0x62Ef93EAa5bB3E47E0e855C323ef156c8E3D8913)
    .transferOwnership(POLYGON_TIMELOCK_ADDRESS);
```

**ステップ 2: UpgradeableBeacon の所有権移譲**

```solidity
UpgradeableBeacon(0xA9D965660dcF0fA73E709fd802e9DEF2d9b52952)
    .transferOwnership(POLYGON_TIMELOCK_ADDRESS);
```

いずれも OpenZeppelin の `OwnableUpgradeable.transferOwnership()` / `Ownable.transferOwnership()` を使用し、即時移譲（単一ステップ）を行う。

#### 移譲後の権限モデル

移譲後、以下の関数はガバナンスパスを通じてのみアクセス可能になる：

**PCEToken（`onlyOwner`）：**

| 関数 | 説明 |
|------|------|
| `mint(address, uint256)` | 新規 PCE トークンのミント |
| `setCommunityTokenAddress(address)` | コミュニティトークン実装の設定 |
| `setNativeTokenToPceTokenRate(uint160)` | ネイティブ→PCE 交換レートの設定 |
| `setMetaTransactionGas(uint256)` | メタトランザクションガスリミットの設定 |
| `setMetaTransactionPriorityFee(uint256)` | メタトランザクション優先手数料の設定 |
| `_authorizeUpgrade(address)` | UUPS プロキシのアップグレード承認 |

**PCECommunityToken beacon（`onlyOwner`）：**

| 関数 | 説明 |
|------|------|
| `upgradeTo(address)` | 全コミュニティトークン実装のアップグレード |

#### ガバナンス実行パス

所有権移譲後、すべての特権操作は以下のパスを辿る：

```
1. Ethereum Governor で提案作成（1,000 WPCE 必要）
2. Ethereum 上で投票期間（約3日間）
3. 承認された場合（定足数: 500,000 WPCE）、Ethereum Timelock にキュー（2日間）
4. Ethereum Timelock が実行 → GovernanceSender → Wormhole Relayer
5. Polygon 上の GovernanceReceiver が受信し、Polygon Timelock にスケジュール（1日間）
6. 誰でも Polygon Timelock のオペレーションを実行可能
```

提案から実行までの最短遅延合計：約6日間（投票3日 + Ethereum Timelock 2日 + Polygon Timelock 1日）。

### Rationale（設計根拠）

**なぜ2ステップ移譲ではなく単一ステップか？**

OpenZeppelin の `OwnableUpgradeable` は単一ステップの `transferOwnership()` を使用する。2ステップ移譲（`acceptOwnership()` 付き）はアドレス入力ミスに対してより安全だが、Polygon Timelock は事前にデプロイ・検証済みの既知のコントラクトである。アドレスミスのリスクは、DEV 環境での事前検証によって軽減される。

**なぜ beacon も別途移譲するのか？**

PCECommunityToken は Beacon Proxy パターンを使用している。beacon の owner はすべてのコミュニティトークンインスタンスのアップグレードを制御する。beacon の owner を Timelock に移譲することで、PCEToken だけでなくコミュニティトークンのアップグレードもガバナンスを通すことが保証される。

**なぜ Polygon Timelock の遅延は1日か？**

Ethereum 側で既に2日間の Timelock 遅延を適用している。Polygon 上の追加1日間は最後の監視ウィンドウとして機能する：悪意ある提案がガバナンスを通過した場合でも、クロスチェーンメッセージ到着後・実行前の1日間でコミュニティが検知・対応できる。

**なぜ `Ownable2Step` を使わないのか？**

PCEToken を `Ownable2StepUpgradeable` に移行するにはコントラクトのアップグレードが必要であり、それは別のスコープである。本提案は既存の `transferOwnership()` インターフェースを使用する。

### Backwards Compatibility（後方互換性）

本提案はコントラクトのインターフェースに変更を加えない。既存のすべての関数は同一の動作を継続する。唯一の動作変更は、`owner()` が Polygon Timelock のアドレスを返すようになるため、`onlyOwner` 関数が旧 EOA owner から呼ばれた場合にリバートすることである。

`PCEToken.createToken()` で作成されたコミュニティトークンは影響を受けない — BeaconProxy インスタンスとしてデプロイされ、個別の owner を持たない。

### Test Cases（テストケース）

**TC-1: 所有権移譲が成功する**

```
前提条件: PCEToken.owner() == EOA
操作: EOA が PCEToken.transferOwnership(timelockAddress) を呼び出す
事後条件: PCEToken.owner() == timelockAddress
```

**TC-2: 旧 owner は onlyOwner 関数を呼べない**

```
前提条件: PCEToken.owner() == timelockAddress
操作: EOA が PCEToken.mint(alice, 1000e18) を呼び出す
期待結果: OwnableUnauthorizedAccount(EOA) でリバート
```

**TC-3: ガバナンス承認済みミントが正常に実行される**

```
前提条件: PCEToken.owner() == timelockAddress
操作:
  1. Ethereum Governor で mint(alice, 1000e18) の提案が投票を通過
  2. Ethereum Timelock にキュー（2日間）
  3. 実行 → GovernanceSender → Wormhole → GovernanceReceiver
  4. Polygon Timelock にスケジュール（1日間）
  5. Polygon Timelock で実行
事後条件: PCEToken.balanceOf(alice) が 1000e18 増加
```

**TC-4: ガバナンス承認済みアップグレードが正常に実行される**

```
前提条件: UpgradeableBeacon.owner() == timelockAddress
操作: beacon.upgradeTo(newImpl) のガバナンス提案がフルパスを通過
事後条件: beacon.implementation() == newImpl
```

### Security Considerations（セキュリティ考慮事項）

**旧 EOA の鍵漏洩**: 所有権移譲後、EOA の秘密鍵は特権アクセスを持たない。漏洩しても攻撃者はコントラクトに影響を与えられない。移譲後の EOA は非機密の鍵として扱うべきである（SHOULD）。

**Wormhole リレー障害**: Wormhole がメッセージの配信に失敗した場合、ガバナンス操作は停滞するが害は生じない。新しいガバナンス提案を通じて操作を再送信できる。

**ガバナンス攻撃（51%投票）**: 500,000 WPCE 以上を集めた攻撃者は悪意ある提案を通過させうる。Ethereum Timelock の2日間 + Polygon Timelock の1日間が合計3日間の検知・対応ウィンドウを提供する。GovernanceReceiver の owner（Polygon Timelock）は `cancelScheduledBatch()` によりスケジュール済み操作をキャンセルできる。

**不可逆性**: EOA による所有権移譲は不可逆である。ガバナンスシステム（Governor、Wormhole、Timelock）に致命的なバグがある場合、復旧には同じシステムを通じたガバナンス提案が必要になる。このリスクは、DEV 環境でのガバナンスインフラの事前デプロイ・テストにより軽減される。

**移譲の順序**: 両方の移譲（PCEToken と beacon）は同一トランザクションバッチまたは連続して実行すべきである（SHOULD）。一方がガバナンス制御でもう一方が EOA 制御という状態の期間を最小化するためである。

### Notes（備考）

**スコープ**: 本提案は所有権移譲のみを対象とする。コントラクトのコード、ストレージレイアウト、関数の動作は変更しない。

**DEV 環境先行**: 移譲は DEV 環境で先に実行しなければならない（MUST）。本番環境の移譲は、完全なガバナンスフロー（提案 → 投票 → 実行 → クロスチェーンリレー → Polygon 実行）の検証成功後に実施する。

**スコープ外:**
- `Ownable2StepUpgradeable` への移行（別途提案の可能性あり）
- ガバナンスパラメータの変更（投票期間、定足数、提案閾値）
- Ethereum↔Polygon 以外のマルチチェーン展開
- フェーズ3のコントラクト移行（別 PIP）
