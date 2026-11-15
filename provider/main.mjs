import { constants, closeSync, fstatSync, lstatSync, openSync, unlinkSync } from 'node:fs';
import { createRequire } from 'node:module';
import { join } from 'node:path';

import { makeReader } from './firewalla.mjs';
import { loginWithDeadline, readBox } from './network.mjs';
import { loadPinnedSdk } from './sdk.mjs';
import { collectObservation, monotonicDeadline, openLedger, privateDir, privateRead, PROVIDER_DEADLINE_MS,
  ProviderError, reserveScheduled, viewLedger } from './state.mjs';
import { safeCode, serve } from './socket.mjs';

const DIR = '/var/db/firewalla-health-provider';
const SOCKET_DIR = '/var/run/firewalla-health';
const CREDENTIALS = '/var/db/hermes-homelab/secrets/firewalla';

function identity() {
  const sampler = privateRead('/opt/firewalla-health-provider/sampler-uid', 11, { uid: 0, mode: 0o644 });
  if (!/^[1-9][0-9]{0,9}\n$/.test(sampler)) throw new ProviderError('configuration');
  const pin = JSON.parse(privateRead(join(DIR, 'identity.json'), 1024));
  if (!pin || Object.keys(pin).length !== 4 || pin.schema_version !== 1
      || !Number.isSafeInteger(pin.samplerUid) || pin.samplerUid <= 0
      || pin.samplerUid === process.getuid() || pin.samplerUid !== Number(sampler)
      || ![pin.groupDigest, pin.targetDigest].every((text) =>
        typeof text === 'string' && /^[a-f0-9]{64}$/.test(text))) {
    throw new ProviderError('configuration');
  }
  return pin;
}

function credentials(withKeys) {
  const value = {
    target: privateRead(join(CREDENTIALS, 'target-host'), 64).trim(),
  };
  if (withKeys) {
    value.publicKey = privateRead(join(CREDENTIALS, 'etp.public.pem'), 8192);
    value.privateKey = privateRead(join(CREDENTIALS, 'etp.private.pem'), 8192);
  }
  return value;
}

function claim(native) {
  const fd = openSync(join(DIR, 'process.lock'),
    constants.O_WRONLY | constants.O_CREAT | constants.O_NOFOLLOW | constants.O_NONBLOCK, 0o600);
  try {
    const info = fstatSync(fd);
    if (!info.isFile() || info.nlink !== 1 || info.uid !== process.getuid()
        || (info.mode & 0o777) !== 0o600 || info.size !== 0) throw new ProviderError('configuration');
    native.claimLock(fd);
    return fd;
  } catch (error) {
    closeSync(fd);
    throw error;
  }
}

function removeStaleSocket(path) {
  let info;
  try {
    info = lstatSync(path);
  } catch (error) {
    if (error.code === 'ENOENT') return;
    throw error;
  }
  // The state lock is held and only the credential owner may create a socket here.
  if (!info.isSocket() || info.uid !== process.getuid() || (info.mode & 0o077) !== 0) {
    throw new ProviderError('configuration');
  }
  unlinkSync(path);
}

async function run(action) {
  if (process.platform !== 'darwin' || process.arch !== 'arm64' || process.getuid() === 0
      || process.versions.node !== '24.20.0') {
    throw new ProviderError('configuration');
  }
  process.umask(0o077);
  privateDir(DIR);
  const pin = identity();
  if (action === 'counts') {
    const state = viewLedger(DIR);
    return { attempts: state.attempts, authentications: state.authentications };
  }
  const native = createRequire(import.meta.url)('./peer.node');
  const lock = claim(native);
  try {
    const ledger = openLedger(DIR);
    const sdk = await loadPinnedSdk();
    const reader = makeReader({
      identity: pin, readCredentials: credentials, secure: sdk.secure, ping: sdk.ping,
      login: (signal, check) => loginWithDeadline(sdk.auth, signal, check), box: readBox,
      reserveAuth: ledger.reserveAuth, reserveRead: (signal) => reserveScheduled(ledger, signal),
    });

    const collect = (signal, check) => collectObservation(reader, ledger, signal, check);

    if (action === 'verify') {
      const timer = AbortSignal.timeout(PROVIDER_DEADLINE_MS);
      const verified = await collect(timer, monotonicDeadline(timer, PROVIDER_DEADLINE_MS));
      return { identity: verified.identity, state: 'observed' };
    }

    privateDir(SOCKET_DIR);
    const path = join(SOCKET_DIR, 'status.sock');
    removeStaleSocket(path);
    let stop;
    const provider = await serve({
      path, uid: pin.samplerUid, native, collect, onRestart: () => stop(),
    });
    await new Promise((done) => {
      let closing;
      stop = () => { closing ??= provider.close().then(done); };
      process.once('SIGTERM', stop);
      process.once('SIGINT', stop);
    });
    return undefined;
  } finally {
    closeSync(lock);
  }
}

const [action, ...other] = process.argv.slice(2);
if (other.length || !['serve', 'verify', 'counts'].includes(action)) {
  console.error('configuration');
  process.exitCode = 2;
} else {
  try {
    const result = await run(action);
    if (result !== undefined) console.log(JSON.stringify(result));
  } catch (error) {
    console.error(safeCode(error));
    process.exitCode = 1;
  }
}
