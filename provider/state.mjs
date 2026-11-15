import { constants, closeSync, fstatSync, fsyncSync, lstatSync, openSync, readSync,
  renameSync, unlinkSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { setTimeout as sleep } from 'node:timers/promises';

export const INTERVAL_MS = 300_000;
export const AUTH_BACKOFF_MS = 900_000;
export const MAX_RESPONSE = 16 * 1024;

export class ProviderError extends Error {
  constructor(code) {
    super(code);
    this.code = code;
  }
}

export function privateDir(path, uid = process.getuid()) {
  const info = lstatSync(path);
  if (!info.isDirectory() || info.uid !== uid || (info.mode & 0o777) !== 0o700) {
    throw new ProviderError('configuration');
  }
}

export function privateRead(path, limit) {
  const fd = openSync(path, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
  try {
    const info = fstatSync(fd);
    if (!info.isFile() || info.uid !== process.getuid() || info.nlink !== 1
        || (info.mode & 0o777) !== 0o600 || info.size > limit) {
      throw new ProviderError('configuration');
    }
    const buffer = Buffer.alloc(limit + 1);
    let size = 0;
    while (size < buffer.length) {
      const count = readSync(fd, buffer, size, buffer.length - size, null);
      if (count === 0) break;
      size += count;
    }
    if (size > limit) throw new ProviderError('configuration');
    return new TextDecoder('utf-8', { fatal: true }).decode(buffer.subarray(0, size));
  } finally {
    closeSync(fd);
  }
}

export async function reserveScheduled(ledger, signal, clock = Date.now) {
  const now = clock();
  const { lastRead } = ledger.view();
  if (lastRead !== null && now >= lastRead) {
    const early = lastRead + INTERVAL_MS - now;
    if (early > 0 && early <= 2000) await sleep(early + 1, undefined, { signal });
  }
  signal.throwIfAborted();
  ledger.reserveRead(clock());
}

function currentLedger(path) {
  let data;
  try {
    data = JSON.parse(privateRead(path, 1024));
  } catch (error) {
    if (error.code === 'ENOENT') {
      return { schema_version: 1, lastRead: null, attempts: 0, lastAuth: null,
        authentications: 0, lastAuthVerified: false, lastObservation: null };
    }
    throw new ProviderError('storage_unavailable');
  }
  if (!data || Object.keys(data).length !== 7 || data.schema_version !== 1
      || !['lastRead', 'lastAuth', 'lastObservation'].every((key) =>
        data[key] === null || (Number.isSafeInteger(data[key]) && data[key] >= 0))
      || !['attempts', 'authentications'].every((key) =>
        Number.isSafeInteger(data[key]) && data[key] >= 0)
      || typeof data.lastAuthVerified !== 'boolean'
      || (data.lastAuth === null && data.lastAuthVerified)
      || (data.lastRead === null) !== (data.attempts === 0)
      || (data.lastAuth === null) !== (data.authentications === 0)) {
    throw new ProviderError('storage_unavailable');
  }
  return data;
}

function persist(dir, path, pending, record) {
  const fd = openSync(pending, constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL, 0o600);
  let renamed = false;
  try {
    try {
      writeFileSync(fd, JSON.stringify(record));
      fsyncSync(fd);
    } finally {
      closeSync(fd);
    }
    renameSync(pending, path);
    renamed = true;
    const parent = openSync(dir, constants.O_RDONLY | constants.O_DIRECTORY | constants.O_NOFOLLOW);
    try {
      fsyncSync(parent);
    } finally {
      closeSync(parent);
    }
  } finally {
    if (!renamed) unlinkSync(pending);
  }
}

export function viewLedger(dir) {
  privateDir(dir);
  return currentLedger(join(dir, 'ledger.json'));
}

export function openLedger(dir) {
  // The production runtime holds a kernel lock on its private state directory
  // before this function is used. A crash can leave at most one uncommitted file.
  privateDir(dir);
  const path = join(dir, 'ledger.json');
  const pending = join(dir, 'ledger.next');
  try {
    privateRead(pending, 1024);
    unlinkSync(pending);
  } catch (error) {
    if (error.code !== 'ENOENT') throw new ProviderError('storage_unavailable');
  }

  function reserve(now, timeKey, countKey, interval, code) {
    const record = currentLedger(path);
    if (!Number.isSafeInteger(now) || now < 0) throw new ProviderError('clock_invalid');
    const minimum = timeKey === 'lastAuth' && record.lastAuthVerified ? INTERVAL_MS : interval;
    if (record[timeKey] !== null && now - record[timeKey] < minimum) {
      throw new ProviderError(code);
    }
    record[timeKey] = now;
    if (timeKey === 'lastAuth') record.lastAuthVerified = false;
    record[countKey] += 1;
    if (!Number.isSafeInteger(record[countKey])) throw new ProviderError('storage_unavailable');
    try {
      persist(dir, path, pending, record);
    } catch {
      throw new ProviderError('storage_unavailable');
    }
  }
  return {
    reserveRead: (now) => reserve(now, 'lastRead', 'attempts', INTERVAL_MS, 'read_limit'),
    reserveAuth: (now) => reserve(now, 'lastAuth', 'authentications', AUTH_BACKOFF_MS, 'auth_backoff'),
    confirmAuth() {
      const record = currentLedger(path);
      if (record.lastAuth === null) throw new ProviderError('storage_unavailable');
      if (!record.lastAuthVerified) {
        record.lastAuthVerified = true;
        try {
          persist(dir, path, pending, record);
        } catch {
          throw new ProviderError('storage_unavailable');
        }
      }
    },
    confirmObservation(seen) {
      const record = currentLedger(path);
      if (!Number.isSafeInteger(seen) || seen < 0
          || (record.lastObservation !== null && seen <= record.lastObservation)) {
        throw new ProviderError('replayed');
      }
      record.lastObservation = seen;
      try {
        persist(dir, path, pending, record);
      } catch {
        throw new ProviderError('storage_unavailable');
      }
    },
    view: () => currentLedger(path),
  };
}
