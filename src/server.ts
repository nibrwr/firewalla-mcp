import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import type { FirewallaClient } from "./firewalla-client.js";
import { registerAlarmTools } from "./tools/alarms.js";
import { registerDeviceTools } from "./tools/devices.js";
import { registerNetworkTools } from "./tools/network.js";
import { registerFlowTools } from "./tools/flows.js";
import { registerRuleTools } from "./tools/rules.js";
import { registerDnsTools } from "./tools/dns.js";
import { registerVlanTools } from "./tools/vlans.js";
import { registerSystemTools } from "./tools/system.js";
import { registerVpnTools } from "./tools/vpn.js";


export function createReadOnlyServer(client: FirewallaClient): McpServer {
  const server = new McpServer({ name: "firewalla-mcp", version: "2.0.0" });
registerAlarmTools(server, client);
registerDeviceTools(server, client);
registerNetworkTools(server, client);
registerFlowTools(server, client);
registerRuleTools(server, client);
registerDnsTools(server, client);
registerVlanTools(server, client);
registerSystemTools(server, client);
registerVpnTools(server, client);

  return server;
}
