import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { openLedger, waitForRead } from '../../provider/state.mjs';

test('one reservation per five minutes survives reopening and does not refill during clock rollback', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'fwh-ledger-'));
  try {
    const first = openLedger(dir);
    const t = 1_800_000_000_000;
    first.reserveRead(t);
    first.reserveAuth(t);
    const next = openLedger(dir);
    for (const now of [t - 1, t, t + 299_999]) {
      assert.throws(() => next.reserveRead(now), { code: 'read_limit' });
    }
    const signal = new AbortController().signal;
    await assert.rejects(waitForRead(next, signal, () => t + 10_000), { code: 'read_limit' });
    await waitForRead(next, signal, () => t + 300_000);
    assert.equal(next.view().attempts, 1);
    assert.throws(() => next.reserveAuth(t + 300_000), { code: 'auth_backoff' });
    next.reserveRead(t + 300_000);
    next.reserveAuth(t + 900_000);
    assert.deepEqual(next.view(), { schema_version: 1, lastRead: t + 300_000, attempts: 2,
      lastAuth: t + 900_000, authentications: 2, lastAuthVerified: false, lastObservation: null });
    next.confirmObservation(t + 299_999);
    assert.equal(next.view().lastAuthVerified, true);
    assert.throws(() => next.reserveAuth(t + 1_199_999), { code: 'auth_backoff' });
    next.reserveAuth(t + 1_200_000);
    const reopened = openLedger(dir);
    assert.throws(() => reopened.confirmObservation(t + 299_999), { code: 'replayed' });
    assert.equal(reopened.view().lastAuthVerified, false);
    assert.throws(() => reopened.reserveAuth(t + 1_500_000), { code: 'auth_backoff' });
    reopened.confirmObservation(t + 300_001);
    assert.equal(reopened.view().lastAuthVerified, true);
    assert.ok(readFileSync(join(dir, 'ledger.json')).length < 1024);
  } finally { rmSync(dir, { recursive: true }); }
});
