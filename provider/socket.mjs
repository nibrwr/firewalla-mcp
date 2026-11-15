import { chmodSync } from 'node:fs';
import { createServer } from 'node:net';
import { MAX_RESPONSE, PROVIDER_DEADLINE_MS, ProviderError, RestartRequired } from './state.mjs';

export const HEALTH_REQUEST = '{"schema_version":1,"operation":"get_network_status"}\n';
const request = Buffer.from(HEALTH_REQUEST);
const ERRORS = new Set(['auth_backoff', 'auth_unavailable', 'clock_invalid', 'configuration',
  'identity_unverified', 'invalid_response', 'read_limit', 'replayed', 'resource_limit',
  'response_too_large', 'source_denied', 'source_unavailable', 'storage_unavailable', 'timeout']);

export function safeCode(error) {
  return error instanceof ProviderError && ERRORS.has(error.code) ? error.code : 'source_unavailable';
}

export async function serve({ path, uid, native, collect, deadlineMs = PROVIDER_DEADLINE_MS, onRestart = () => {} }) {
  let active;
  let draining = false;
  const server = createServer({ allowHalfOpen: true }, (peer) => {
    peer.on('error', () => peer.destroy());
    try {
      // Node does not publish peer credentials. The pinned Darwin runtime exposes
      // the accepted pipe descriptor here, which the native shim checks.
      if (active || draining || native.peerUid(peer._handle?.fd) !== uid) {
        peer.destroy();
        return;
      }
    } catch {
      peer.destroy();
      return;
    }

    const controller = new AbortController();
    let handling = false;
    let bytes = 0;
    active = { peer, controller };

    function release() {
      clearTimeout(timer);
      if (active?.peer === peer) active = undefined;
    }

    function reply(result) {
      if (peer.destroyed || peer.writableEnded) return;
      const frame = JSON.stringify(result) + '\n';
      if (Buffer.byteLength(frame) > MAX_RESPONSE) peer.destroy();
      else peer.end(frame);
    }

    const timer = setTimeout(() => {
      controller.abort(new ProviderError('timeout'));
      if (handling) reply({ schema_version: 1, error: 'timeout' });
      else peer.destroy();
    }, deadlineMs);
    peer.on('close', () => {
      controller.abort(new ProviderError('source_unavailable'));
      if (!handling) release();
    });
    peer.on('data', (chunk) => {
      if (bytes + chunk.length > request.length || !chunk.equals(request.subarray(bytes, bytes + chunk.length))) {
        peer.destroy();
        return;
      }
      bytes += chunk.length;
    });
    peer.on('end', async () => {
      if (peer.destroyed || controller.signal.aborted || bytes !== request.length) {
        peer.destroy();
        return;
      }
      handling = true;
      try {
        const result = await collect(controller.signal);
        if (!controller.signal.aborted) reply(result);
      } catch (error) {
        if (error instanceof RestartRequired) {
          draining = true;
          if (peer.destroyed) onRestart();
          else {
            peer.once('close', onRestart);
            if (controller.signal.aborted) peer.destroy();
          }
        }
        if (!controller.signal.aborted) reply({ schema_version: 1, error: safeCode(error) });
      } finally {
        release();
      }
    });
  });

  server.on('error', () => {
    active?.controller.abort(new ProviderError('source_unavailable'));
    active?.peer.destroy();
  });
  try {
    await new Promise((resolve, reject) => {
      server.once('error', reject);
      server.listen(path, 1, () => {
        server.removeListener('error', reject);
        resolve();
      });
    });
    chmodSync(path, 0o600);
    native.grantSocket(path, uid);
  } catch (error) {
    if (server.listening) await new Promise((resolve) => server.close(resolve));
    throw error;
  }

  return {
    close() {
      return new Promise((resolve) => {
        active?.controller.abort(new ProviderError('source_unavailable'));
        active?.peer.destroy();
        server.close(resolve);
      });
    },
  };
}
