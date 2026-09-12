"""Real local producer -> promotion -> export -> diagnostic graph read.

Requires separately installed client/org gate wheels; no network service needed.
Uses a disposable local gate, not a tenant API or a current-authority decision.
"""
from dataclasses import asdict
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from doublegate import keys
from doublegate_org.daemon import OrgDaemon
from doublegate_sdk.knowledge_graph import GraphQuery, OrgGraphDiagnosticReader


def main():
    with TemporaryDirectory(prefix='dg-graph-') as directory:
        home = Path(directory)
        (home / 'config.toml').write_text(
            '[deployment]\nrole="org"\n[auth]\nmode="key"\n'
            '[lifecycle]\nscan_delay_s=0\n[gating]\nauto_grade=false\n'
            '[quorum]\nprofile="shared"\n')
        keys.save_operator(keys.generate(), home)
        daemon = OrgDaemon(home)
        try:
            admin = daemon.apikeys.issue(daemon.ledger.append, label='local example',
                scopes=['admin'], spaces=[], issued_by='example')
            def admit(body, parents=()):
                aid = daemon.ingest(content=body.encode(), content_type='memory',
                    trust_class='T-1', source_uri='example://graph',
                    writer_identity='example:writer', derives_from=list(parents))['artifact_id']
                daemon.sign(aid, 'promote', 'example:reviewer-a')
                daemon.sign(aid, 'promote', 'example:reviewer-b')
                assert daemon.promote(aid, 'example:integrator')['state'] == 'ACTIVE'
                return aid
            parent = admit('The source specification sets a retry budget.')
            child = admit('The summary cites that specification.', (parent,))
            before = daemon.ledger.tip_hash
            result = OrgGraphDiagnosticReader(daemon).read(GraphQuery((child,)), api_key=admin['raw'])
            assert daemon.ledger.tip_hash == before
            assert {n.artifact_id for n in result.nodes} == {parent, child}
            print(json.dumps({
                'mode': 'admin diagnostic; not current-authority eligibility',
                'connection_id': result.connection_id,
                'nodes': [{'identity_domain': n.identity_domain, 'artifact_id': n.artifact_id,
                           'space': n.space, 'content_type': n.content_type, 'body': n.body,
                           'provenance': dict(n.provenance)} for n in result.nodes],
                'edges': [asdict(e) for e in result.edges],
            }, indent=2))
        finally:
            daemon.close()


if __name__ == '__main__':
    main()
