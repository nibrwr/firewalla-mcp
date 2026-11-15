import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdtempSync, lstatSync, rmSync } from 'node:fs';
import { connect } from 'node:net';
import { join } from 'node:path';
import test from 'node:test';
import { serve, HEALTH_REQUEST } from '../../provider/socket.mjs';

const native = createRequire(import.meta.url)(process.env.PROVIDER_TEST_NATIVE);
const UID = process.getuid();
const SAMPLE = { schema_version: 1, operation: 'get_network_status', identity: 'verified',
  uptime: 904.5, timestamp: '2027-01-15T07:59:59.000Z' };

function exchange(path, request) {
  return new Promise((resolve, reject) => {
    const client = connect(path);
    const chunks = [];
    client.setTimeout(1000, () => client.destroy(new Error('fixture client timeout')));
    client.on('connect', () => client.end(request));
    client.on('data', (chunk) => chunks.push(chunk));
    client.on('error', (error) => {
      if (error.code === 'ECONNRESET' || error.code === 'EPIPE') resolve(null);
      else reject(error);
    });
    client.on('close', () => {
      const raw = Buffer.concat(chunks).toString('utf-8');
      resolve(raw ? JSON.parse(raw) : null);
    });
  });
}

async function withServer(handler, check, { uid = UID, deadlineMs = 250 } = {}) {
  const dir = mkdtempSync('/private/tmp/fwh-ipc-');
  const path = join(dir, 'status.sock');
  let service;
  try {
    service = await serve({ path, uid, native, collect: handler, deadlineMs });
    await check(path);
  } finally {
    if (service) await service.close();
    rmSync(dir, { recursive: true });
  }
}

test('real Darwin peer and ACL admit only the fixed health request', async () => {
  let attempts = 0;
  await withServer(async () => { attempts += 1; return SAMPLE; }, async (path) => {
    assert.equal(lstatSync(path).mode & 0o777, 0o600);
    for (const bad of ['{}', HEALTH_REQUEST + HEALTH_REQUEST, ' '.repeat(16385)]) {
      assert.equal(await exchange(path, bad), null);
    }
    assert.deepEqual(await exchange(path, HEALTH_REQUEST), SAMPLE);
    assert.equal(attempts, 1);
  });
});

test('mismatched peer is rejected before creating an upstream attempt', async () => {
  let attempts = 0;
  // Pass a test-native adapter for ACL installation only. Identity still comes
  // from the real Darwin getpeereid call on the connected peer.
  const dir = mkdtempSync('/private/tmp/fwh-ipc-');
  const path = join(dir, 'status.sock');
  const service = await serve({ path, uid: UID + 1, native: { peerUid: native.peerUid, grantSocket() {} },
    collect: async () => { attempts += 1; return SAMPLE; } });
  try {
    assert.equal(await exchange(path, HEALTH_REQUEST), null);
    assert.equal(attempts, 0);
  } finally {
    await service.close();
    rmSync(dir, { recursive: true });
  }
});

test('deadline aborts the active request and admits a later connection', async () => {
  let calls = 0;
  let wasAborted = false;
  await withServer(async (signal) => {
    if (++calls > 1) return SAMPLE;
    await new Promise((_, reject) => signal.addEventListener('abort', () => {
      wasAborted = true;
      reject(signal.reason);
    }, { once: true }));
  }, async (path) => {
    assert.deepEqual(await exchange(path, HEALTH_REQUEST), { schema_version: 1, error: 'timeout' });
    assert.equal(wasAborted, true);
    assert.deepEqual(await exchange(path, HEALTH_REQUEST), SAMPLE);
    assert.equal(calls, 2);
  }, { deadlineMs: 60 });
});

test('concurrent requests cannot cause another upstream read', async () => {
  let calls = 0;
  let release, notify;
  const started = new Promise((resolve) => { notify = resolve; });
  const settled = new Promise((resolve) => { release = resolve; });
  await withServer(async () => { calls += 1; notify(); await settled; return SAMPLE; }, async (path) => {
    const first = exchange(path, HEALTH_REQUEST);
    await started;
    try {
      assert.equal(await exchange(path, HEALTH_REQUEST), null);
      assert.equal(calls, 1);
    } finally { release(); }
    assert.deepEqual(await first, SAMPLE);
  });
});
