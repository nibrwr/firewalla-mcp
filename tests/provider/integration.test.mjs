import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { mkdtempSync, mkdirSync, rmSync } from 'node:fs';
import { createRequire } from 'node:module';
import { join } from 'node:path';
import { promisify } from 'node:util';
import test from 'node:test';
import { openLedger } from '../../provider/state.mjs';
import { serve } from '../../provider/socket.mjs';

const native = createRequire(import.meta.url)(process.env.PROVIDER_TEST_NATIVE);
const exec = promisify(execFile);
const py = [
  'from pathlib import Path',
  'import json,os,sys',
  'from sampler.firewalla_health import sampler,transport',
  'at=int(sys.argv[3])',
  'outcome=sampler.sample(Path(sys.argv[1]), lambda: transport.local_status(Path(sys.argv[2]),os.getuid()), clock=lambda:at, require_heap_limit=True)',
  'print(json.dumps([outcome,sampler.status(Path(sys.argv[1]),now=at)]))',
].join('\n');

test('real sampler transport and SQLite keep both readings across provider and sampler reopening', async () => {
  const root = mkdtempSync('/private/tmp/fwh-bridge-');
  const path = join(root, 'status.sock');
  const privateState = join(root, 'provider-state');
  mkdirSync(privateState, { mode: 0o700 });
  const sampleState = join(root, 'samples');
  let at = 1_800_000_000;
  let provider;
  try {
    for (const [uptime, count] of [[650, 1], [12, 2]]) {
      const ledger = openLedger(privateState);
      provider = await serve({
        path, uid: process.getuid(), native,
        collect: async () => {
          ledger.reserveRead(at * 1000);
          return { schema_version: 1, operation: 'get_network_status', identity: 'verified', uptime,
            timestamp: new Date((at - 1) * 1000).toISOString() };
        },
      });
      const { stdout, stderr } = await exec('/opt/homebrew/bin/python3',
        ['-E', '-s', '-B', '-c', py, sampleState, path, String(at)], { timeout: 5000 });
      assert.equal(stderr, '');
      const [outcome, evidence] = JSON.parse(stdout);
      assert.equal(outcome, 'observed');
      assert.equal(evidence.reads_reserved_today, count);
      assert.equal(evidence.health.reported_uptime_s, uptime);
      assert.equal(ledger.view().attempts, count);
      assert.deepEqual(evidence.incidents.map((item) => item.kind), count === 2 ? ['possible_process_restart'] : []);
      await provider.close();
      provider = undefined;
      at += 300;
    }
  } finally {
    if (provider) await provider.close();
    rmSync(root, { recursive: true });
  }
});
