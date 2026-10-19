import os from "node:os";
import path from "node:path";
import { createInterface } from "node:readline";
import {
  askThroughBroker,
  type BrokerClientOptions,
  contextThroughBroker,
  diagnosticTraceThroughBroker,
  grantedToolsThroughBroker,
  mattersThroughBroker,
  readHostCredentialFile,
  recallThroughBroker,
  searchConversationsThroughBroker,
} from "./broker-client.js";
import { resolveHostWorkspacePath } from "./host-workspace.js";
import { processMcpLine } from "./mcp-protocol.js";
import { McpSession } from "./mcp-session.js";

const discoveryPath =
  process.env.ONE_MEMORY_BROKER_DISCOVERY ??
  path.join(os.homedir(), ".one-memory", "integrations", "cursor-broker.json");
const credentialFile = process.env.ONE_MEMORY_HOST_CREDENTIAL_FILE;
const clientOptions: BrokerClientOptions =
  credentialFile === undefined
    ? {}
    : { credentialProvider: () => readHostCredentialFile(credentialFile) };
// Resolved here rather than inside the session: reading the environment is
// the entry point's job, and a session that did it could not be tested
// without one.
const session = new McpSession(
  discoveryPath,
  (discovery, request) =>
    recallThroughBroker(discovery, request, clientOptions),
  resolveHostWorkspacePath({
    env: process.env,
    cwd: process.cwd(),
    home: os.homedir(),
  }),
  (discovery, request) => askThroughBroker(discovery, request, clientOptions),
  (discovery, request) =>
    searchConversationsThroughBroker(discovery, request, clientOptions),
  (discovery, request) =>
    mattersThroughBroker(discovery, request, clientOptions),
  (discovery, request, signal) =>
    contextThroughBroker(discovery, request, { ...clientOptions, signal }),
  undefined,
  undefined,
  (discovery, request) =>
    diagnosticTraceThroughBroker(discovery, request, clientOptions),
  // What the grant allows, so the list a host sees is the list it may use.
  // Unreadable means every tool stays listed; a call is still refused on
  // arrival if the grant does not allow it.
  (discovery) => grantedToolsThroughBroker(discovery, clientOptions),
  process.env.ONE_MEMORY_GENERIC_LOCAL_MCP === "1",
);
const lines = createInterface({
  input: process.stdin,
  crlfDelay: Number.POSITIVE_INFINITY,
});

lines.on("line", (line) => {
  void handleLine(line);
});

async function handleLine(line: string): Promise<void> {
  const response = await processMcpLine(session, line);
  if (response) process.stdout.write(response);
}
