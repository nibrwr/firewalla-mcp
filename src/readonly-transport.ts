import type { Transport } from "@modelcontextprotocol/sdk/shared/transport.js";

const reads = new Set(["get_devices", "get_top_talkers", "get_clients_by_network", "get_offline_devices", "get_device_flows", "search_flows", "get_dns_queries", "get_alarms", "get_audit_logs", "get_rules", "get_target_lists", "get_features", "get_network_status", "get_network_stats", "get_network_performance", "get_wan_usage", "get_vlans", "get_system_info", "get_vpn_status"]);

/** Reject unknown tools before SDK dispatch, independently of SDK tool-error wrapping. */
export function readOnlyTransport(inner: Transport): Transport {
  const guarded: Transport = {
    async start() {
      inner.onmessage = (message, extra) => {
        if ("method" in message && message.method === "tools/call" && "id" in message && !reads.has(String(message.params?.name))) {
          void inner.send({ jsonrpc: "2.0", id: message.id, error: { code: -32601, message: "Unknown read-only tool" } }).catch(error => guarded.onerror?.(error instanceof Error ? error : new Error("Protocol send failed")));
          return;
        }
        guarded.onmessage?.(message, extra);
      };
      inner.onerror = error => guarded.onerror?.(error);
      inner.onclose = () => guarded.onclose?.();
      await inner.start();
    },
    send: (message, options) => inner.send(message, options),
    close: () => inner.close(),
  };
  return guarded;
}
