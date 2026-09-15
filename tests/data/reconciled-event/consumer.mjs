// Independent bounded fixture consumer. No SDK or Python imports.
import fs from 'node:fs';
import crypto from 'node:crypto';
import assert from 'node:assert/strict';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const root = path.dirname(fileURLToPath(import.meta.url));
const read = name => fs.readFileSync(path.join(root, 'generated', name));
const wire = JSON.parse(read('wire.json'));
const manifest = JSON.parse(read('manifest.json'));
const canonical = value => {
  if (value === null || typeof value === 'boolean') return JSON.stringify(value);
  if (typeof value === 'string') {
    assert.equal(value.normalize('NFC'), value);
    return JSON.stringify(value);
  }
  if (typeof value === 'number') {
    assert(Number.isSafeInteger(value) && value >= 0);
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) return '[' + value.map(canonical).join(',') + ']';
  assert.equal(typeof value, 'object');
  return '{' + Object.keys(value).sort().map(k => canonical(k)+':'+canonical(value[k])).join(',') + '}';
};
const sha = b => crypto.createHash('sha256').update(b).digest('hex');
const key = phase => crypto.createPublicKey({key: Buffer.concat([
  Buffer.from('302a300506032b6570032100', 'hex'), Buffer.from(manifest.keys[phase], 'hex')]),
  format: 'der', type: 'spki'});
const bytes = Buffer.from(canonical(wire.document));
assert.deepEqual(bytes, read('event.canonical.bin'));
assert.equal('sha256:'+sha(bytes), wire.artifact_id);
assert.equal(manifest.artifact_id, wire.artifact_id);
assert(!('artifact_id' in wire.document));
assert(!('signature' in wire.document));
const signingInput = Buffer.concat([Buffer.from('doublegate.submission.v1\0'), bytes]);
assert(crypto.verify(null, signingInput, key('submission'), Buffer.from(wire.signature,'base64url')));
const proofs = wire.document.attribution_chain.map((jws, index) => {
  const parts = jws.split('.'); assert.equal(parts.length,3);
  const phase = index === 0 ? 'production' : 'submission';
  const header = JSON.parse(Buffer.from(parts[0],'base64url'));
  assert.deepEqual(header,{alg:'EdDSA',kid:phase,typ:'doublegate-attribution+jws'});
  assert(crypto.verify(null, Buffer.from(parts[0]+'.'+parts[1]), key(phase), Buffer.from(parts[2],'base64url')));
  return JSON.parse(Buffer.from(parts[1],'base64url'));
});
assert.equal(proofs[1].parent_statement_digest, sha(Buffer.from(wire.document.attribution_chain[0])));
assert.equal(manifest.chain_digest, sha(Buffer.from(canonical(wire.document.attribution_chain))));
assert.equal(proofs[0].envelope_digest, sha(Buffer.from(canonical(wire.document.envelope))));
assert.equal(proofs[1].envelope_digest, proofs[0].envelope_digest);
assert(!('artifact_id' in proofs[0])); assert(!('artifact_id' in proofs[1]));
assert.notEqual(proofs[0].actor_id, proofs[1].actor_id);
for (const field of ['tenant_id','operation_id','requester_id','contributor_id','home_team_id','evidence_digest']) {
  assert.equal(proofs[0][field], proofs[1][field]);
}
const evidence = JSON.parse(read('production-evidence.json'));
assert.equal(wire.document.content_digest, 'sha256:'+sha(Buffer.from(evidence.content_hex,'hex')));
assert.equal(proofs[0].evidence_digest, sha(Buffer.from(canonical(evidence.manifest))));
const changed = structuredClone(wire.document); changed.review_refs.push({stage:'local',record_digest:'a'.repeat(64)});
assert.notEqual('sha256:'+sha(Buffer.from(canonical(changed))), wire.artifact_id);
assert(!crypto.verify(null, Buffer.concat([Buffer.from('doublegate.submission.v1\0'),Buffer.from(canonical(changed))]),
  key('submission'),Buffer.from(wire.signature,'base64url')));
console.log('PASS: independent Node canonical bytes, event/content/envelope/chain hashes, Ed25519 event + two JWS, parent/role links, mutation refusal');
console.log('artifact_id='+wire.artifact_id);
