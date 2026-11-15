import assert from 'node:assert/strict';
import test from 'node:test';

import { makeReader, identityDigest } from '../../provider/firewalla.mjs';
import { monotonicDeadline, ProviderError } from '../../provider/state.mjs';

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

function fixture({ groups = [group], host = address, response = answer, rejected, delay = 0,
  reserveDuration = 0 } = {}) {
  const calls = [];
  let ticks = 0;
  let current = clock;
  const reader = makeReader({
    identity: { groupDigest: identityDigest(groupId), targetDigest: identityDigest(address) },
    readCredentials: () => ({ target: host, publicKey: 'fixture-public', privateKey: 'fixture-private' }),
    secure: {
      importKeyPairFromString() { calls.push('load'); },
      rsaDecrypt(cipher) { assert.equal(cipher, 'paired-rotated'); return 'k'.repeat(32); },
      aesEncrypt(input, key) { assert.equal(key, 'k'.repeat(32)); return input; },
      aesDecrypt(input) { return input; },
    },
    ping: (eid) => ({ operation: 'ping', eid, message: { obj: { id: 'fixture-ping-' + ticks } } }),
    login: async () => {
      calls.push('cloud');
      current += delay;
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
      const number = ticks++;
      if (number === 0 && rejected === 'http') throw new ProviderError('source_denied');
      if (number === 0 && rejected === 'envelope') return { status: 'expired' };
      if (number === 0 && rejected === 'transient') return { error: 'fixture-temporary-failure' };
      const out = number === 0 ? response
        : { ...response, data: { ...response.data, timestamp: '2027-01-15T07:59:59.500Z' } };
      return { message: JSON.stringify({ replyid: JSON.parse(body.message).message.obj.id, ...out }) };
    },
    reserveAuth: () => calls.push('reserve-auth'),
    reserveRead: async () => {
      if (delay || reserveDuration) calls.push(['reserve-read', current]);
      current += reserveDuration;
    },
    clock: () => current,
  });
  return { reader, calls, now: () => current };
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

test('first observation must carry the ID of the ping that generated it', async () => {
  const { reader } = fixture({ response: { ...answer, replyid: 'earlier-ping' } });
  await assert.rejects(reader(new AbortController().signal), { code: 'identity_unverified' });
});

test('a rejected LAN session is refreshed at the next call without retrying the first call', async () => {
  for (const [rejected, code] of [['http', 'source_denied'], ['envelope', 'source_denied']]) {
    const { reader, calls } = fixture({ rejected });
    const signal = new AbortController().signal;
    await assert.rejects(reader(signal), { code });
    assert.equal(calls.filter((call) => call === 'lan').length, 1);
    await reader(signal);
    assert.equal(calls.filter((call) => call === 'cloud').length, 2);
    assert.equal(calls.filter((call) => call === 'lan').length, 2);
  }
});

test('a transient error envelope keeps the verified session for the next eligible read', async () => {
  const { reader, calls } = fixture({ rejected: 'transient' });
  const signal = new AbortController().signal;
  await assert.rejects(reader(signal), { code: 'source_unavailable' });
  await reader(signal);
  assert.deepEqual(calls, ['load', 'reserve-auth', 'cloud', 'lan', 'lan']);
});

test('the LAN reservation starts after slow authentication and before dispatch', async () => {
  const { reader, calls } = fixture({ delay: 4000 });
  await reader(new AbortController().signal);
  assert.deepEqual(calls, ['load', 'reserve-auth', 'cloud', ['reserve-read', clock + 4000], 'lan']);
});

test('a reservation that finishes after the deadline cannot dispatch', async () => {
  const { reader, calls, now } = fixture({ delay: 8_990, reserveDuration: 15 });
  const signal = new AbortController().signal;
  const check = monotonicDeadline(signal, 9_000, now);
  await assert.rejects(reader(signal, check), { code: 'timeout' });
  assert.equal(signal.aborted, false);
  assert.deepEqual(calls, ['load', 'reserve-auth', 'cloud', ['reserve-read', clock + 8_990]]);
});
