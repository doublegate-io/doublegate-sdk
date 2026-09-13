"""Optional source-mode integration test; requires Client Gate, not an SDK dependency.

Run with the SDK and maintained client-gate src directories on PYTHONPATH.
The reviewer is the product's deterministic StubBackend, not a live model.
"""
from pathlib import Path
import json
import tempfile
import threading
import time

from doublegate.daemon import Daemon
from doublegate_sdk.client import GateClient, GateError, UnixSocketTransport


def run(parent):
    root = Path(tempfile.mkdtemp(prefix='sdk-life-', dir=parent))
    home = root / 'gate'
    home.mkdir(mode=0o700)
    (home / 'config.toml').write_text('[console]\nenabled=false\n[gating]\nbackend="stub"\nauto_grade=true\n[lifecycle]\nscan_delay_s=0\n')
    daemon = Daemon(home)
    thread = threading.Thread(target=daemon.serve,
        kwargs={'sock_path': home/'daemon.sock', 'tick_interval_s': 31536000}, daemon=True)
    thread.start()
    writer = GateClient(UnixSocketTransport(home/'daemon.sock', allow_proposals=True))
    reader = GateClient(UnixSocketTransport(home/'daemon.sock'))
    try:
        deadline = time.monotonic() + 8
        while True:
            try:
                reader.status()
                break
            except GateError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(.05)
        text = 'Basalt specimen registry uses durable labels for the laboratory collection.'
        proposal = writer.propose(text, content_type='memory', trust_class='T-4', source_uri='fixture://sdk-lifecycle')
        aid = proposal['artifact_id']
        assert reader.status(aid)['state'] == 'L1_SCANNED'
        assert reader.recall('Basalt')['hits'] == []
        assert reader.recall('Basalt', include_provisional=True)['hits'] == []
        repeated = writer.propose(text, content_type='memory', trust_class='T-4', source_uri='fixture://sdk-lifecycle')
        assert repeated['duplicate'] is True and repeated['artifact_id'] == aid
        tick = daemon.tick()
        assert reader.status(aid)['state'] == 'ACTIVE', tick
        assert reader.recall('Basalt')['hits'] == []  # Solo admission is provisional.
        hits = reader.recall('Basalt', include_provisional=True)['hits']
        assert len(hits) == 1 and hits[0]['artifact_id'] == aid
        assert hits[0]['provisional'] is True and hits[0]['body'] == text
        result = {'passed': True, 'artifact_id': aid, 'pending_excluded': True,
                  'duplicate_same_id': True, 'admitted_state': 'ACTIVE',
                  'provisional_requires_opt_in': True, 'same_record_recalled': True,
                  'reviewer': 'deterministic product StubBackend; no live inference',
                  'scope': 'two SDK clients, same local peer; not cross-user authorization or organization delivery',
                  'sdk_origin': __import__('doublegate_sdk.client', fromlist=['x']).__file__,
                  'gate_origin': __import__('doublegate.daemon', fromlist=['x']).__file__}
        (root/'result.json').write_text(json.dumps(result, indent=2)+'\n')
        print(json.dumps(result, indent=2))
        print('Evidence:', root/'result.json')
    finally:
        daemon.stop()
        thread.join(timeout=10)
        if thread.is_alive():
            raise RuntimeError('Owned fixture did not stop')
        daemon.close()


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--output-parent', type=Path, required=True)
    args = parser.parse_args()
    run(args.output_parent)
