import { AsyncLocalStorage } from 'node:async_hooks';
import { request as http } from 'node:http';
import { request as https } from 'node:https';
import { isPrivateTarget } from './firewalla.mjs';
import { MAX_RESPONSE, ProviderError } from './state.mjs';

const cloudDeadline = new AsyncLocalStorage();
const LOGIN = 'https://firewalla.encipher.io/app/api/v2/login/eptoken';

export async function boundedJson(response, signal) {
  const length = response.headers['content-length'];
  const encoding = response.headers['content-encoding'];
  if (length !== undefined && (!/^\d+$/.test(length) || Number(length) > MAX_RESPONSE)) {
    throw new ProviderError('response_too_large');
  }
  if (encoding !== undefined && encoding !== 'identity') throw new ProviderError('invalid_response');
  const data = Buffer.alloc(MAX_RESPONSE);
  let size = 0;
  for await (const chunk of response) {
    signal.throwIfAborted();
    if (size + chunk.length > MAX_RESPONSE) throw new ProviderError('response_too_large');
    chunk.copy(data, size);
    size += chunk.length;
  }
  signal.throwIfAborted();
  try {
    return JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(data.subarray(0, size)));
  } catch {
    throw new ProviderError('invalid_response');
  }
}

async function post(url, body, signal, token) {
  signal.throwIfAborted();
  const headers = {
    'Content-Type': 'application/json', 'Accept-Encoding': 'identity',
    'Content-Length': Buffer.byteLength(body),
  };
  if (token !== undefined) headers.Authorization = 'Bearer ' + token;
  return new Promise((resolve, reject) => {
    const request = (url.protocol === 'https:' ? https : http)(url, {
      method: 'POST', headers, agent: false, signal, maxHeaderSize: 8192,
    }, (response) => {
      const status = response.statusCode;
      if (status !== 200) {
        const rejected = token !== undefined || status === 401 || status === 403;
        reject(new ProviderError(rejected ? 'source_denied' : 'source_unavailable'));
        response.destroy();
        return;
      }
      boundedJson(response, signal).then(resolve, (error) => {
        reject(error instanceof ProviderError ? error : new ProviderError(signal.aborted ? 'timeout' : 'source_unavailable'));
        response.destroy();
      });
    });
    request.on('error', () => reject(new ProviderError(signal.aborted ? 'timeout' : 'source_unavailable')));
    request.end(body);
  });
}

// This adapter is imported only by the pinned SDK's cloud-login module.
export default async function cloudFetch(url, options) {
  const signal = cloudDeadline.getStore();
  if (!signal || url !== LOGIN || options?.method !== 'POST'
      || typeof options.body !== 'string' || Buffer.byteLength(options.body) > MAX_RESPONSE) {
    throw new ProviderError('configuration');
  }
  const value = await post(new URL(LOGIN), options.body, signal);
  return { json: async () => value };
}

export function loginWithDeadline(sdk, signal) {
  return cloudDeadline.run(signal, () => sdk.login());
}

export function readBox(url, body, token, signal) {
  if (url.protocol !== 'http:' || !isPrivateTarget(url.hostname) || url.port !== '8833'
      || !/^\/v1\/encipher\/message\/[A-Za-z0-9_-]{1,128}$/.test(url.pathname)
      || url.search || url.hash || url.username || url.password) {
    throw new ProviderError('configuration');
  }
  return post(url, JSON.stringify(body), signal, token);
}
