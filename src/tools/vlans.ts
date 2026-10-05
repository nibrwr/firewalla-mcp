import { READ_ONLY_TOOL_ANNOTATIONS } from "./annotations.js";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { FirewallaClient } from "../firewalla-client.js";
import { sanitizeNetworkConfig } from "../sanitize.js";

export function registerVlanTools(server: McpServer, client: FirewallaClient) {
  server.tool(
    "get_vlans",
    "List network segments, VLANs, and network groups configured on the Firewalla. Shows network names, subnets, VLAN IDs, and which interfaces they're on.",
    READ_ONLY_TOOL_ANNOTATIONS,
    async () => {
      try {
        const result = await client.getNetworkConfig();
        return {
          content: [
            {
              type: "text" as const,
              text: JSON.stringify(sanitizeNetworkConfig(result), null, 2),
            },
          ],
        };
      } catch (error) {
        const message = "Read-only Firewalla operation failed";
        try {
          const initData = await client.getInit();
          const networkInfo = {
            interfaces: initData.interfaces,
            networkConfig: initData.networkConfig,
            tags: initData.tags,
          };
          return {
            content: [
              {
                type: "text" as const,
                text: JSON.stringify(sanitizeNetworkConfig(networkInfo), null, 2),
              },
            ],
          };
        } catch (fallbackError) {
          return {
            content: [{ type: "text" as const, text: `Error fetching VLANs: ${message}` }],
            isError: true,
          };
        }
      }
    },
  );
}
