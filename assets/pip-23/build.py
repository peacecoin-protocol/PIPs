"""Rebuild the PIP-23 snapshot, LP allocation and distribution table from saved logs.

Usage: python3 -I build.py [DATA_DIR]
DATA_DIR defaults to the directory of this script. Inputs are read from
DATA_DIR/inputs and outputs are written to DATA_DIR/out. No network access.
"""
import csv
import json
import sys
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent
IN, OUT = ROOT / 'inputs', ROOT / 'out'
SNAPSHOT_BLOCK = 25904711
LEGACY_PCE = '0x7c28310cc0b8d898c57b93913098e74a3ba23228'
LEGACY_TOTAL = 222156723490000000000000000
ZERO = '0x' + '0' * 40
TRANSFER = '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef'
WPCE = '0xeb5e0632ed3c635e0fa07420a328b49a7d0e6e6d'
PAIRS = ['0x56a62dd43f135bad525df9179b0ef4fad5639ca1', '0x3ce03c46a5de51e7ea690eec54c0eaf0d8028323']


def fmt(wei):
    return f'{Decimal(wei) / Decimal(10**18):.18f}'


def ordered(logs):
    return sorted(logs, key=lambda x: (int(x['blockNumber'], 16), int(x['logIndex'], 16)))


def replay(logs, token, to_block):
    """Return balances and total supply of `token` after `to_block`."""
    bal, supply = defaultdict(int), 0
    for log in ordered(logs):
        assert not log.get('removed', False)
        assert log['address'].lower() == token and log['topics'][0] == TRANSFER
        if int(log['blockNumber'], 16) > to_block:
            break
        src, dst = '0x' + log['topics'][1][-40:], '0x' + log['topics'][2][-40:]
        value = int(log['data'], 16)
        if src == ZERO:
            supply += value
        else:
            bal[src] -= value
            assert bal[src] >= 0, (token, src)
        if dst == ZERO and src != ZERO:
            supply -= value
        else:
            # Transfer(0, 0, v) is Uniswap V2's permanently locked minimum liquidity.
            bal[dst] += value
    return {a: v for a, v in bal.items() if v}, supply


def write(name, header, rows):
    with open(OUT / name, 'w', newline='') as f:
        w = csv.writer(f, lineterminator='\n')
        w.writerow(header)
        w.writerows(rows)


def main():
    OUT.mkdir(exist_ok=True)
    raw = json.loads((IN / 'legacy_pce_transfer_logs.json').read_text())
    logs = raw['logs']
    snap, supply = replay(logs, LEGACY_PCE, SNAPSHOT_BLOCK)
    assert supply == LEGACY_TOTAL == sum(snap.values())
    # No mint or burn after the snapshot within the saved logs.
    _, latest_supply = replay(logs, LEGACY_PCE, raw['latest'])
    assert latest_supply == LEGACY_TOTAL
    snap_rows = sorted(snap.items(), key=lambda x: (-x[1], x[0]))
    write('snapshot_25904711.csv', ['address', 'balance_wei', 'balance'], [(a, v, fmt(v)) for a, v in snap_rows])

    alloc = {a: v for a, v in snap.items() if a not in PAIRS}
    excluded = []
    lp_rows = []
    for pair in PAIRS:
        lp_logs = json.loads((IN / f'lp_logs_{pair}.json').read_text())
        lp, lp_supply = replay(lp_logs, pair, SNAPSHOT_BLOCK)
        assert lp_supply == sum(lp.values())
        pair_pce, given = snap[pair], 0
        for holder, amount in sorted(lp.items()):
            share = pair_pce * amount // lp_supply
            given += share
            lp_rows.append((pair, holder, amount, lp_supply, share, fmt(share)))
            if holder == ZERO:
                excluded.append((holder, share, f'uniswap_minimum_liquidity_locked:{pair}'))
            else:
                alloc[holder] = alloc.get(holder, 0) + share
        excluded.append(('-', pair_pce - given, f'lp_pro_rata_rounding:{pair}'))
    # WPCE: look through to the WPCE holders at the snapshot (PIP-23, Specification 4).
    wpce_logs = json.loads((IN / 'wpce_transfer_logs.json').read_text())
    assert wpce_logs['to_block'] >= SNAPSHOT_BLOCK
    wpce, wpce_supply = replay(wpce_logs['logs'], WPCE, SNAPSHOT_BLOCK)
    assert wpce_supply == sum(wpce.values()) and WPCE not in wpce
    backing = alloc.pop(WPCE)
    assert backing >= wpce_supply
    wpce_rows = []
    for holder, amount in sorted(wpce.items(), key=lambda x: (-x[1], x[0])):
        alloc[holder] = alloc.get(holder, 0) + amount
        wpce_rows.append((holder, amount, fmt(amount)))
    if backing > wpce_supply:
        excluded.append((WPCE, backing - wpce_supply, 'wpce_unwrapped_excess'))
    write('wpce_allocation_25904711.csv', ['wpce_holder', 'pce_allocation_wei', 'pce_allocation'], wpce_rows)
    lp_rows.sort(key=lambda r: (-r[4], r[1], r[0]))
    write('lp_allocation_25904711.csv', ['pair', 'lp_holder', 'lp_balance', 'lp_total_supply', 'pce_allocation_wei', 'pce_allocation'], lp_rows)

    dist = sorted(alloc.items(), key=lambda x: (-x[1], x[0]))
    assert all(v > 0 for _, v in dist) and len({a for a, _ in dist}) == len(dist)
    assert sum(alloc.values()) + sum(v for _, v, _ in excluded) == LEGACY_TOTAL
    write('distribution_25904711.csv', ['address', 'amount_wei', 'amount', 'note'],
          [(a, v, fmt(v), '') for a, v in dist])
    excluded.sort(key=lambda r: (-r[1], r[0], r[2]))
    write('excluded_25904711.csv', ['address', 'amount_wei', 'amount', 'reason'], [(a, v, fmt(v), r) for a, v, r in excluded])
    print(f'holders={len(snap)} recipients={len(dist)} distributed={sum(alloc.values())} excluded={sum(v for _, v, _ in excluded)}')


if __name__ == '__main__':
    main()
