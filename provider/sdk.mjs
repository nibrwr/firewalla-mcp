import { createHash } from 'node:crypto';
import { lstatSync, readdirSync, readFileSync } from 'node:fs';
import { isBuiltin, registerHooks } from 'node:module';
import { extname, join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { ProviderError } from './state.mjs';

const ROOT = '/opt/hermes-homelab/firewalla-mcp/node_modules/node-firewalla';
const LOGIN = 'lib/esm/api/FWGroupApi.js';
const SECURE = 'lib/esm/utils/SecureUtil.js';
const MESSAGE = 'lib/esm/models/FWCmdMessage.js';
const TREE = '43cf4ac97737acbc1cc868897b9c8969b06f56d6eb236f5d5908981d2f39cbdd';
const kinds = new Set(['.js', '.mjs', '.json']);
const digest = (data) => createHash('sha256').update(data).digest('hex');

export function verifiedModules(root, expected, owner = 0) {
  const entries = [];
  const sources = new Map();
  const classify = (name) => {
    const file = lstatSync(join(root, name));
    if (file.uid !== owner || (file.mode & 0o022) || (!file.isFile() && !file.isDirectory())) {
      throw new ProviderError('configuration');
    }
    return file;
  };

  function record(name, file) {
    if (!file.isFile() || file.size > 65_536 || entries.length >= 64) {
      throw new ProviderError('configuration');
    }
    const path = join(root, name);
    const bytes = readFileSync(path);
    if (bytes.length !== file.size) throw new ProviderError('configuration');
    entries.push([name, digest(bytes)]);
    if (name.endsWith('.js') || name.endsWith('.mjs')) {
      sources.set(pathToFileURL(path).href, bytes);
    }
  }

  function visit(name, depth) {
    if (depth > 5 || !classify(name).isDirectory()) throw new ProviderError('configuration');
    for (const item of readdirSync(join(root, name))) {
      const child = name + '/' + item;
      const file = classify(child);
      if (file.isDirectory()) visit(child, depth + 1);
      else if (kinds.has(extname(item))) record(child, file);
    }
  }

  if (!classify('.').isDirectory() || !classify('lib').isDirectory()) {
    throw new ProviderError('configuration');
  }
  record('package.json', classify('package.json'));
  try {
    record('lib/package.json', classify('lib/package.json'));
  } catch (error) {
    if (error.code !== 'ENOENT') throw error;
  }
  visit('lib/esm', 0);
  entries.sort(([a], [b]) => (a < b ? -1 : a !== b ? 1 : 0));
  if (digest(JSON.stringify(entries)) !== expected) throw new ProviderError('configuration');
  return sources;
}

export async function loadPinnedSdk() {
  const at = (name) => pathToFileURL(join(ROOT, name)).href;
  const sources = verifiedModules(ROOT, TREE);
  const sdkPrefix = at('lib/esm') + '/';
  const boundedFetch = new URL('./network.mjs', import.meta.url).href;
  const hooks = registerHooks({
    resolve(specifier, context, next) {
      if (context.parentURL?.startsWith(sdkPrefix)) {
        if (specifier === 'node-fetch') return { url: boundedFetch, shortCircuit: true };
        if (isBuiltin(specifier)) return next(specifier, context);
        const resolved = next(specifier, context);
        if (!sources.has(resolved.url)) throw new ProviderError('configuration');
        return resolved;
      }
      return next(specifier, context);
    },
    load(url, context, next) {
      if (sources.has(url)) return { source: sources.get(url), format: 'module', shortCircuit: true };
      if (url.startsWith(sdkPrefix)) throw new ProviderError('configuration');
      return next(url, context);
    },
  });
  try {
    const { default: auth } = await import(at(LOGIN));
    const { default: secure } = await import(at(SECURE));
    const { FWCmdMessage } = await import(at(MESSAGE));
    return { auth, secure, ping: (eid) => new FWCmdMessage('ping').toJSON(eid) };
  } finally {
    hooks.deregister();
  }
}
