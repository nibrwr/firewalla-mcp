import assert from 'node:assert/strict';
import test from 'node:test';

import { makeReader, identityDigest } from '../../provider/firewalla.mjs';

const clock = 1_800_000_000_000;
const address = '192.168.255.3'; // Fictional private address; intercepted by the test.
const groupId = 'fixture-group-a';
const group = {
  _id: groupId, eid: 'fixture-pair', symmetricKeys: [
    { eid: 'other-pair', key: 'unrelated-key' },
    { eid: 'fixture-pair', rkey: '{"key":"paired-rotated","ts":1700000000}' },
  ],
};
const answer = { code: 200, data: { uptime: 904.5, timestamp: '2027-01-15T07:59:59.000Z' } };

function fixture({ groups = [group], host = address, response = answer } = {}) {
  const calls = [];
  let ticks = 0;
  const reader = makeReader({
    identity: { groupDigest: identityDigest(groupId), targetDigest: identityDigest(address) },
    readCredentials: () => ({ target: host, publicKey: 'fixture-public', privateKey: 'fixture-private' }),
    secure: {
      importKeyPairFromString() { calls.push('load'); },
      rsaDecrypt(cipher) { assert.equal(cipher, 'paired-rotated'); return 'k'.repeat(32); },
      aesEncrypt(input, key) { assert.equal(key, 'k'.repeat(32)); return input; },
      aesDecrypt(input) { return input; },
    },
    ping: (eid) => ({ operation: 'ping', eid }),
    login: async () => {
      calls.push('cloud');
      return { access_token: 'fixture-token', groups };
    },
    box: async (url, body, authorization) => {
      calls.push('lan');
      assert.equal(url.hostname, address);
      assert.equal(url.port, '8833');
      assert.equal(url.pathname, '/v1/encipher/message/fixture-group-a');
      assert.equal(authorization, 'fixture-token');
      assert.equal(body.rkeyts, 1700000000);
      assert.equal(JSON.parse(body.message).operation, 'ping');
      const out = ticks++ === 0 ? response
        : { ...response, data: { ...response.data, timestamp: '2027-01-15T07:59:59.500Z' } };
      return { message: JSON.stringify(out) };
    },
    reserveAuth: () => calls.push('reserve-auth'),
    clock: () => clock,
  });
  return { reader, calls };
}

test('selects only the unique pinned group and its exact paired key, then reuses the session', async () => {
  const { reader, calls } = fixture({ groups: [{ ...group, _id: 'other-group' }, group] });
  const signal = new AbortController().signal;
  assert.deepEqual(await reader(signal), {
    schema_version: 1, operation: 'get_network_status', identity: 'verified',
    uptime: 904.5, timestamp: '2027-01-15T07:59:59.000Z',
  });
  await reader(signal);
  assert.deepEqual(calls, ['load', 'reserve-auth', 'cloud', 'lan', 'lan']);
});

test('wrong physical target fails before credentials are submitted to the cloud', async () => {
  const { reader, calls } = fixture({ host: '192.168.255.4' });
  await assert.rejects(reader(new AbortController().signal), { code: 'identity_unverified' });
  assert.deepEqual(calls, []);
});

test('no first-group or first-key fallback, even when only one group is returned', async () => {
  for (const groups of [[{ ...group, _id: 'other-group' }], [group, group],
    [{ ...group, symmetricKeys: [{ eid: 'other-pair', key: 'not-the-owner-key' }] }]]) {
    const { reader, calls } = fixture({ groups });
    await assert.rejects(reader(new AbortController().signal), { code: 'identity_unverified' });
    assert.equal(calls.includes('lan'), false);
  }
});

test('a decrypted response without a fresh valid ping cannot attest the box', async () => {
  const signal = new AbortController().signal;
  for (const data of [null, { ...answer.data, uptime: -1 }, { ...answer.data, timestamp: '2027-01-15T08:00:10.000Z' },
    { ...answer.data, timestamp: '2027-01-15T07:50:00.000Z' }, { uptime: 89, timestamp: answer.data.timestamp, name: 'private' }]) {
    const { reader } = fixture({ response: { code: 200, data } });
    await assert.rejects(reader(signal), { code: 'invalid_response' });
  }
});
