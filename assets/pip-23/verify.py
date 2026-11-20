"""Check the saved logs and the snapshot of PIP-23 against an Ethereum archive node.

Usage: RPC_URL=<archive node URL> ETHERSCAN_API_KEY=<key> python3 -I verify.py [DATA_DIR]

0. Checks that the node is on Ethereum mainnet and that the snapshot block
   has the hash given in PIP-23.
1. Fetches the Transfer logs of each token up to the snapshot block from the
   Etherscan API and checks that they equal the saved logs.
2. For every address in those logs, checks that balanceOf at the snapshot
   block equals the replayed balance, and that these balances sum to
   totalSupply at the snapshot block.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from build import LEGACY_PCE, PAIRS, SNAPSHOT_BLOCK, TRANSFER, WPCE, ordered, replay  # noqa: E402

RPC_URL = os.environ['RPC_URL']
ETHERSCAN_API_KEY = os.environ['ETHERSCAN_API_KEY']
SNAPSHOT_HASH = '0x6dd75f8317957156f05c731d9ffee046d23b14813dc55ffc3e4dd7c065c41b74'
# Pairs cannot exist before the legacy PCE, so their search also starts at its creation block.
CREATION = {LEGACY_PCE: 10219538, WPCE: 23302929, PAIRS[0]: 10219538, PAIRS[1]: 10219538}


def rpc(method, params):
    body = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params}).encode()
    req = urllib.request.Request(RPC_URL, body, {'Content-Type': 'application/json'})
    time.sleep(0.2)  # stay under public rate limits
    for attempt in range(8):
        try:
            res = json.load(urllib.request.urlopen(req, timeout=60))
            break
        except urllib.error.HTTPError as e:
            if e.code != 429 or attempt == 7:
                raise
            time.sleep(5 * 2 ** attempt)
    if 'error' in res:
        raise RuntimeError(res['error'])
    return res['result']


def get_logs(token, start, end):
    """Fetch Transfer logs from the Etherscan API, 1,000 per request."""
    logs = []
    while True:
        url = (f'https://api.etherscan.io/v2/api?chainid=1&module=logs&action=getLogs&address={token}'
               f'&topic0={TRANSFER}&fromBlock={start}&toBlock={end}&page=1&offset=1000&apikey={ETHERSCAN_API_KEY}')
        res = json.load(urllib.request.urlopen(url, timeout=60))
        page = res['result'] if isinstance(res['result'], list) else []
        if res['status'] != '1' and res['message'] != 'No records found':
            raise RuntimeError(res)
        if len(page) < 1000:
            return logs + page
        # Restart from the last block of a full page; drop its partial block to avoid duplicates.
        last = int(page[-1]['blockNumber'], 16)
        logs += [log for log in page if int(log['blockNumber'], 16) < last]
        start = last


def hexint(value):
    # Etherscan returns "0x" for zero.
    return int(value, 16) if value != '0x' else 0


def key(log):
    return (hexint(log['blockNumber']), log['blockHash'].lower(), hexint(log['logIndex']), log['transactionHash'].lower(),
            tuple(t.lower() for t in log['topics']), hexint(log['data']))


def call_uint(token, data):
    return int(rpc('eth_call', [{'to': token, 'data': data}, hex(SNAPSHOT_BLOCK)]), 16)


def check(token, saved):
    saved = [log for log in saved if int(log['blockNumber'], 16) <= SNAPSHOT_BLOCK]
    fetched = get_logs(token, CREATION[token], SNAPSHOT_BLOCK)
    print(f'{token}: fetched {len(fetched)} logs', flush=True)
    assert sorted(map(key, fetched)) == sorted(map(key, saved)), f'{token}: logs differ'
    bal, supply = replay(ordered(saved), token, SNAPSHOT_BLOCK)
    # The zero address is included: Uniswap V2 pairs lock their minimum liquidity there.
    addresses = {'0x' + t[-40:] for log in saved for t in log['topics'][1:3]}
    balances = {a: call_uint(token, '0x70a08231' + a[2:].rjust(64, '0')) for a in sorted(addresses)}
    for a, b in balances.items():
        assert b == bal.get(a, 0), f'{token}: {a}'
    total = call_uint(token, '0x18160ddd')
    assert total == supply == sum(balances.values()), f'{token}: totalSupply'
    print(f'{token}: {len(saved)} logs, {len(addresses)} addresses, totalSupply {total}: OK')


def check_chain():
    assert int(rpc('eth_chainId', []), 16) == 1, 'not Ethereum mainnet'
    block = rpc('eth_getBlockByNumber', [hex(SNAPSHOT_BLOCK), False])
    assert block['hash'].lower() == SNAPSHOT_HASH, 'snapshot block hash differs'
    print(f'chain 1, block {SNAPSHOT_BLOCK} {SNAPSHOT_HASH}: OK', flush=True)


def main():
    check_chain()
    inputs = ROOT / 'inputs'
    check(LEGACY_PCE, json.loads((inputs / 'legacy_pce_transfer_logs.json').read_text())['logs'])
    check(WPCE, json.loads((inputs / 'wpce_transfer_logs.json').read_text())['logs'])
    for pair in PAIRS:
        check(pair, json.loads((inputs / f'lp_logs_{pair}.json').read_text()))


if __name__ == '__main__':
    main()
