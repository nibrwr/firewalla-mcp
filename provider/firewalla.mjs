import { createHash } from 'node:crypto';
import { isIPv4 } from 'node:net';
import { MAX_RESPONSE, ProviderError } from './state.mjs';

const GROUP_ID = /^[A-Za-z0-9_-]{1,128}$/;
const TOKEN = /^[A-Za-z0-9_.=+\/-]{1,4096}$/;
const UTC = /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{3})?Z$/;
const MAX_AGE_MS = 600_000;

export function identityDigest(text) {
  return createHash('sha256').update(text, 'utf8').digest('hex');
}

export function isPrivateTarget(value) {
  if (typeof value !== 'string' || !isIPv4(value)) return false;
  const [a, b] = value.split('.').map(Number);
  return a === 10 || (a === 192 && b === 168) || (a === 172 && b >= 16 && b <= 31);
}

function selectGroup(groups, digest) {
  const matching = Array.isArray(groups) ? groups.filter((group) =>
    typeof group?._id === 'string' && GROUP_ID.test(group._id) && identityDigest(group._id) === digest) : [];
  if (matching.length !== 1) throw new ProviderError('identity_unverified');
  const [group] = matching;
  const keys = typeof group.eid === 'string' && GROUP_ID.test(group.eid) && Array.isArray(group.symmetricKeys)
    ? group.symmetricKeys.filter((record) => record?.eid === group.eid) : [];
  if (keys.length !== 1) throw new ProviderError('identity_unverified');
  return { id: group._id, eid: group.eid, key: keys[0] };
}

function keyMaterial(secure, selected) {
  try {
    const rotated = selected.rkey === undefined ? null : JSON.parse(selected.rkey);
    const cipher = rotated === null ? selected.key : rotated.key;
    if (typeof cipher !== 'string' || !cipher || cipher.length > 4096
        || (rotated !== null && (!Number.isSafeInteger(rotated.ts) || rotated.ts <= 0))) {
      throw new ProviderError('identity_unverified');
    }
    const key = secure.rsaDecrypt(cipher);
    if (typeof key !== 'string' || key.length < 32 || Buffer.byteLength(key.slice(0, 32)) !== 32) {
      throw new ProviderError('identity_unverified');
    }
    return { key, rotatedAt: rotated?.ts };
  } catch {
    throw new ProviderError('identity_unverified');
  }
}

function health(data, now) {
  const { uptime, timestamp } = data ?? {};
  if (!data || Object.keys(data).length !== 2 || typeof uptime !== 'number'
      || !Number.isFinite(uptime) || uptime < 0 || uptime > 1e10
      || typeof timestamp !== 'string' || !UTC.test(timestamp)) {
    throw new ProviderError('invalid_response');
  }
  const seen = new Date(timestamp);
  const normalized = timestamp.length === 20 ? timestamp.replace('Z', '.000Z') : timestamp;
  if (!Number.isFinite(seen.getTime()) || seen.toISOString() !== normalized
      || seen.getTime() > now || now - seen.getTime() >= MAX_AGE_MS) {
    throw new ProviderError('invalid_response');
  }
  return { schema_version: 1, operation: 'get_network_status', identity: 'verified', timestamp, uptime };
}

export function makeReader({ identity, readCredentials, secure, ping, login, box, reserveAuth, reserveRead = () => {}, clock = Date.now }) {
  let session;
  let lastSeen;

  return async function readHealth(signal) {
    signal.throwIfAborted();
    const credentials = readCredentials(!session);
    const { target } = credentials;
    if (!isPrivateTarget(target) || identityDigest(target) !== identity.targetDigest) {
      session = undefined;
      throw new ProviderError('identity_unverified');
    }
    if (!session) {
      secure.importKeyPairFromString(credentials.publicKey, credentials.privateKey);
      reserveAuth(clock());
      const authorization = await login(signal);
      const group = selectGroup(authorization?.groups, identity.groupDigest);
      const token = authorization?.access_token;
      if (typeof token !== 'string' || !TOKEN.test(token)) throw new ProviderError('auth_unavailable');
      session = { token, group, ...keyMaterial(secure, group.key) };
    }
    signal.throwIfAborted();
    const { token, group, key, rotatedAt } = session;
    const command = ping(group.eid);
    const requestId = command?.message?.obj?.id;
    if (typeof requestId !== 'string' || !GROUP_ID.test(requestId)) {
      throw new ProviderError('configuration');
    }
    await reserveRead(signal);
    signal.throwIfAborted();
    const body = {
      timestamp: Math.floor(clock() / 1000),
      message: secure.aesEncrypt(JSON.stringify(command), key),
    };
    if (rotatedAt !== undefined) body.rkeyts = rotatedAt;
    let response;
    try {
      const url = new URL('http://' + target + ':8833/v1/encipher/message/' + group.id);
      response = await box(url, body, token, signal);
    } catch (error) {
      if (error.code === 'source_denied') session = undefined;
      throw error;
    }
    if (response?.error) {
      session = undefined;
      throw new ProviderError('source_denied');
    }
    if (typeof response?.message !== 'string') {
      session = undefined;
      throw new ProviderError('invalid_response');
    }
    let decrypted;
    try {
      const raw = secure.aesDecrypt(response.message, key);
      if (Buffer.byteLength(raw) > MAX_RESPONSE) throw new ProviderError('response_too_large');
      decrypted = JSON.parse(raw);
    } catch {
      session = undefined;
      throw new ProviderError('invalid_response');
    }
    signal.throwIfAborted();
    if (decrypted?.code !== 200) {
      session = undefined;
      throw new ProviderError('source_denied');
    }
    if (decrypted.replyid !== requestId) throw new ProviderError('identity_unverified');
    const output = health(decrypted.data, clock());
    const seen = Date.parse(output.timestamp);
    if (lastSeen !== undefined && seen <= lastSeen) throw new ProviderError('replayed');
    lastSeen = seen;
    return output;
  };
}
