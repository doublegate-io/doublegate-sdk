"""Optional source-mode integration probe; requires Client Gate, not an SDK dependency.

Run with the SDK and maintained client-gate src directories on PYTHONPATH.
The reviewer is the product's deterministic StubBackend, not a live model.
An agent's knowledge client proposes and reads; nothing here decides.
"""
from pathlib import Path
import json
import tempfile
import threading
import time

from doublegate.daemon import Daemon
from doublegate_sdk.errors import GateError
from doublegate_sdk.knowledge import KnowledgeClient
from doublegate_sdk.transport import UnixSocketTransport


def run(parent):
    root = Path(tempfile.mkdtemp(prefix='sdk-life-', dir=parent))
    home = root / 'gate'
    home.mkdir(mode=0o700)
    (home / 'config.toml').write_text('[console]\nenabled=false\n[gating]\nbackend="stub"\nauto_grade=true\n[lifecycle]\nscan_delay_s=0\n')
    daemon = Daemon(home)
    thread = threading.Thread(target=daemon.serve,
        kwargs={'sock_path': home/'daemon.sock', 'tick_interval_s': 31536000}, daemon=True)
    thread.start()
    writer = KnowledgeClient(UnixSocketTransport(home/'daemon.sock', scope='knowledge'))
    reader = KnowledgeClient(UnixSocketTransport(home/'daemon.sock'))
    try:
        deadline = time.monotonic() + 8
        while not reader.present():
            if time.monotonic() >= deadline:
                raise RuntimeError('the gate did not come up')
            time.sleep(.05)
        text = 'Basalt specimen registry uses durable labels for the laboratory collection.'
        proposal = writer.remember(text, content_type='memory', trust_class='T-4', source_uri='fixture://sdk-lifecycle')
        aid = proposal.artifact_id
        assert reader.status(aid)['state'] == 'L1_SCANNED'
        assert reader.recall('Basalt')['hits'] == []
        assert reader.recall('Basalt', include_provisional=True)['hits'] == []
        repeated = writer.remember(text, content_type='memory', trust_class='T-4', source_uri='fixture://sdk-lifecycle')
        assert repeated.duplicate is True and repeated.artifact_id == aid
        try:
            reader.remember('x', content_type='memory', source_uri='fixture://read-only')
            raise AssertionError('a read-scoped transport proposed')
        except GateError as exc:
            assert exc.kind == 'writes_disabled'
        tick = daemon.tick()
        assert reader.status(aid)['state'] == 'ACTIVE', tick
        assert reader.recall('Basalt')['hits'] == []  # Solo admission is provisional.
        hits = reader.recall('Basalt', include_provisional=True)['hits']
        assert len(hits) == 1 and hits[0]['artifact_id'] == aid
        assert hits[0]['provisional'] is True and hits[0]['body'] == text
        learning = writer.learn('Basalt labels are durable.', source_uri='agent://probe', evidence=[aid])
        assert learning.state == 'L1_SCANNED' and learning.derives_from == (aid,)
        assert reader.why(learning.artifact_id)['artifact_id'] == learning.artifact_id
        result = {'passed': True, 'artifact_id': aid, 'learning_id': learning.artifact_id,
                  'pending_excluded': True, 'duplicate_same_id': True, 'admitted_state': 'ACTIVE',
                  'provisional_requires_opt_in': True, 'same_record_recalled': True,
                  'read_scope_cannot_propose': True,
                  'reviewer': 'deterministic product StubBackend; no live inference',
                  'scope': 'two SDK clients, same local peer; not cross-user authorization or organization delivery',
                  'sdk_origin': __import__('doublegate_sdk.knowledge', fromlist=['x']).__file__,
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
