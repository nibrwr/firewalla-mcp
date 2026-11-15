import assert from 'node:assert/strict';
import { Readable } from 'node:stream';
import test from 'node:test';
import cloudFetch, { boundedJson, loginWithDeadline, readBox } from '../../provider/network.mjs';
import { loadPinnedSdk } from '../../provider/sdk.mjs';

function response(chunks, headers = {}) {
  return Object.assign(Readable.from(chunks), { headers });
}

test('caps a chunked HTTP body before parsing, and rejects content encoding and invalid bytes', async () => {
  const signal = new AbortController().signal;
  const frame = Buffer.from('{"v":1}' + ' '.repeat(16_384 - 7));
  assert.deepEqual(await boundedJson(response([frame]), signal), { v: 1 });
  await assert.rejects(boundedJson(response([frame, Buffer.from(' ')]), signal), { code: 'response_too_large' });
  await assert.rejects(boundedJson(response([], { 'content-length': '16385' }), signal), { code: 'response_too_large' });
  await assert.rejects(boundedJson(response([], { 'content-encoding': 'gzip' }), signal), { code: 'invalid_response' });
  await assert.rejects(boundedJson(response([Buffer.from([255])]), signal), { code: 'invalid_response' });
});

test('production cloud and LAN adapters reject alternate endpoints before I/O', async () => {
  const signal = new AbortController().signal;
  await assert.rejects(loginWithDeadline({
    login: () => cloudFetch('https://example.invalid', { method: 'POST', body: '{}' }),
  }, signal), { code: 'configuration' });
  assert.throws(() => readBox(new URL('http://127.0.0.1:8833/v1/encipher/message/g'), {}, '', signal),
    { code: 'configuration' });
});

test('the installed pinned SDK creates only a ping command in this adapter', async () => {
  const { ping } = await loadPinnedSdk();
  const call = ping('fixture-owner').message;
  assert.equal(call.obj.mtype, 'cmd');
  assert.equal(call.obj.data.item, 'ping');
  assert.deepEqual(call.obj.data.value, {});
});

test('real pinned crypto authenticates a fictional group and emits only the health fields', async () => {
  const { generateKeyPairSync, publicEncrypt } = await import('node:crypto');
  const { identityDigest, makeReader } = await import('../../provider/firewalla.mjs');
  const { secure, ping } = await loadPinnedSdk();
  const { publicKey, privateKey } = generateKeyPairSync('rsa', {
    modulusLength: 2048,
    publicKeyEncoding: { format: 'pem', type: 'spki' },
    privateKeyEncoding: { format: 'pem', type: 'pkcs8' },
  });
  const key = 'this-is-a-fictional-session-key-for-tests';
  const encrypted = publicEncrypt(publicKey, Buffer.from(key)).toString('base64');
  const target = '192.168.255.13';
  const group = 'fictional-attested-group';
  const now = 1_800_000_000_000;
  const at = '2027-01-15T07:59:59.000Z';
  let calls = 0;
  const read = makeReader({
    identity: { groupDigest: identityDigest(group), targetDigest: identityDigest(target) },
    readCredentials: () => ({ target, publicKey, privateKey }), secure, ping,
    login: async () => ({ access_token: 'fictional-token', groups: [{
      _id: group, eid: 'fictional-pair', symmetricKeys: [{ eid: 'fictional-pair', key: encrypted }],
    }] }),
    reserveAuth() {},
    clock: () => now,
    box: async (_, body) => {
      calls += 1;
      const opened = JSON.parse(secure.aesDecrypt(body.message, key));
      assert.equal(opened.message.obj.data.item, 'ping');
      return { message: secure.aesEncrypt(JSON.stringify({ code: 200, data: { timestamp: at, uptime: 500 } }), key) };
    },
  });
  assert.deepEqual(await read(new AbortController().signal), {
    schema_version: 1, operation: 'get_network_status', identity: 'verified', uptime: 500, timestamp: at,
  });
  assert.equal(calls, 1);
});
