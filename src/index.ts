import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { FirewallaClient } from "./firewalla-client.js";
import { readOnlyTransport } from "./readonly-transport.js";
import { createReadOnlyServer } from "./server.js";
const FIREWALLA_IP = process.env.FIREWALLA_IP;
const GROUP_ID = process.env.FIREWALLA_GROUP_ID;
const PUBLIC_KEY_PATH = process.env.FIREWALLA_PUBLIC_KEY_PATH;
const PRIVATE_KEY_PATH = process.env.FIREWALLA_PRIVATE_KEY_PATH;

if (!FIREWALLA_IP || !GROUP_ID || !PUBLIC_KEY_PATH || !PRIVATE_KEY_PATH) {
  console.error(
    "Explicit FIREWALLA_IP, FIREWALLA_GROUP_ID and paired key paths are required",
  );
  process.exit(1);
}

const client = new FirewallaClient(FIREWALLA_IP, PUBLIC_KEY_PATH, PRIVATE_KEY_PATH, GROUP_ID);
const server = createReadOnlyServer(client);

async function main() {
  try {
    await client.connect();
    console.error("Connected to Firewalla at", FIREWALLA_IP);
  } catch {
    console.error("Firewalla authentication or box connection failed; no provider details logged.");
    process.exitCode = 1;
    return;
  }

  const transport = new StdioServerTransport();
  await server.connect(readOnlyTransport(transport));
  console.error("Firewalla MCP server running on stdio");
}

main().catch(() => {
  console.error("Firewalla MCP startup failed; no provider details logged.");
  process.exitCode = 1;
});
