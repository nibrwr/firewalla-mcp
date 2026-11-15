import { createHash } from 'node:crypto';
import { lstatSync, readFileSync } from 'node:fs';
import { registerHooks } from 'node:module';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { ProviderError } from './state.mjs';

const ROOT = '/opt/hermes-homelab/firewalla-mcp/node_modules/node-firewalla/lib/esm';
const LOGIN = 'api/FWGroupApi.js';
const SECURE = 'utils/SecureUtil.js';
const MESSAGE = 'models/FWCmdMessage.js';
const PINS = {
  [LOGIN]: '2edc6140f7c5ead69754b8b07f0c2d7e68daf9ec9b44373c207dd2b4369f6922',
  [SECURE]: 'c79b1a7edb750813ba9d97d0c6f8bd1e5056ff765afaf639c1bcec88a4cd0900',
  [MESSAGE]: 'a5bdfff67bfadeb1168c0ff1eba13b30010e905591aa4e314f0ff4f9c8d74d47',
  'models/FWMessage.js': '3a45abe2a1d23f62cd268daa9815ccceeebfa72f3ff2bdd5aca965b979e99979',
};

export async function loadPinnedSdk() {
  const at = (name) => pathToFileURL(join(ROOT, name)).href;
  for (const [name, expected] of Object.entries(PINS)) {
    const path = join(ROOT, name);
    const file = lstatSync(path);
    if (!file.isFile() || file.uid !== 0 || (file.mode & 0o022) || file.size > 65_536
        || createHash('sha256').update(readFileSync(path)).digest('hex') !== expected) {
      throw new ProviderError('configuration');
    }
  }
  const authUrl = at(LOGIN);
  const sdkPrefix = pathToFileURL(ROOT + '/').href;
  const boundedFetch = new URL('./network.mjs', import.meta.url).href;
  const hooks = registerHooks({
    resolve(specifier, context, next) {
      // Nothing under the pinned package gets a general-purpose fetch client
      // in this process. The adapter only admits the existing login operation.
      if (context.parentURL?.startsWith(sdkPrefix) && specifier === 'node-fetch') {
        return { url: boundedFetch, shortCircuit: true };
      }
      return next(specifier, context);
    },
  });
  try {
    const { default: auth } = await import(authUrl);
    const { default: secure } = await import(at(SECURE));
    const { FWCmdMessage } = await import(at(MESSAGE));
    return { auth, secure, ping: (eid) => new FWCmdMessage('ping').toJSON(eid) };
  } finally {
    hooks.deregister();
  }
}
