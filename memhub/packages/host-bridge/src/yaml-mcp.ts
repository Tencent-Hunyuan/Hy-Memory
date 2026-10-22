/** Minimal YAML editing for hosts that keep MCP servers in a settings document.
 *
 * Same bargain as `toml-mcp.ts`: the file is edited as text rather than parsed
 * and re-emitted, because a host's settings document is the user's own file and
 * a round-trip through a generic YAML emitter rewrites bytes we were never
 * asked to touch. Only the one entry under `mcp_servers` that this installer
 * owns is ever rewritten.
 *
 * Reading has to be more forgiving than writing. A host may write an entry
 * with the command folded across two lines because the path is long and
 * contains a space:
 *
 *     command: /Applications/Example.app/Contents/MacOS/Example
 *       Runtime
 *
 * That is one plain scalar, not two, so a reader that takes the first line at
 * face value would compare a truncated path against the one it installed,
 * decide the entry belongs to someone else, and refuse to touch it.
 */

export interface YamlServer {
  command: string;
  args: string[];
  env: Record<string, string>;
}

const TOP_LEVEL_KEY = /^([^\s#][^:]*):/;
const SECTION = "mcp_servers";

interface Section {
  /** Line index of the `mcp_servers:` line itself. */
  header: number;
  /** Line indices of the section's body, trailing blank lines excluded. */
  body: number[];
}

function findSection(
  lines: string[],
  sectionName: string = SECTION,
): Section | null {
  const header = lines.findIndex((line) => {
    const match = TOP_LEVEL_KEY.exec(line);
    return match !== null && match[1] === sectionName;
  });
  if (header === -1) return null;
  const body: number[] = [];
  for (let index = header + 1; index < lines.length; index += 1) {
    const line = lines[index];
    // A line starting in column zero opens the next top-level key, or is a
    // comment that describes it. Either way the section has ended.
    if (line !== "" && !/^\s/.test(line)) break;
    body.push(index);
  }
  // Blank lines at the tail separate this section from what follows; they are
  // not ours, so removing the section must leave them behind.
  while (body.length > 0 && lines[body[body.length - 1]].trim() === "") {
    body.pop();
  }
  return { header, body };
}

/** Indent of the section's own children, taken from the file rather than assumed. */
function childIndent(lines: string[], body: number[]): string {
  for (const index of body) {
    const line = lines[index];
    if (line.trim() === "") continue;
    return /^\s*/.exec(line)?.[0] ?? "  ";
  }
  return "  ";
}

interface Entry {
  name: string;
  /** Line indices of the entry, its own key line included. */
  lines: number[];
}

function entries(lines: string[], section: Section): Entry[] {
  const indent = childIndent(lines, section.body);
  const found: Entry[] = [];
  for (const index of section.body) {
    const line = lines[index];
    if (line.trim() === "") continue;
    const own = /^\s*/.exec(line)?.[0] ?? "";
    if (own !== indent) continue;
    const match = /^\s*("?)([^"\s][^:"]*)\1\s*:/.exec(line);
    if (!match) continue;
    found.push({ name: match[2], lines: [index] });
  }
  for (let position = 0; position < found.length; position += 1) {
    const start = found[position].lines[0];
    const next =
      found[position + 1]?.lines[0] ??
      section.body[section.body.length - 1] + 1;
    found[position].lines = section.body.filter(
      (index) => index >= start && index < next,
    );
  }
  return found;
}

/** Fields of one entry, plain scalars unfolded and block sequences collected. */
function fields(lines: string[], entry: Entry): Map<string, string[]> {
  const own = entry.lines.slice(1);
  if (own.length === 0) return new Map();
  const indent = childIndent(lines, own);
  const collected = new Map<string, string[]>();
  let current: string[] | null = null;
  let currentKey: string | null = null;
  for (const index of own) {
    const line = lines[index];
    if (line.trim() === "") continue;
    const lineIndent = /^\s*/.exec(line)?.[0] ?? "";
    const match = /^\s*("?)([^"\s][^:"]*)\1\s*:(.*)$/.exec(line);
    if (lineIndent === indent && match) {
      currentKey = match[2];
      current = [];
      collected.set(currentKey, current);
      const inline = match[3].trim();
      if (inline !== "") current.push(inline);
      continue;
    }
    if (current === null) continue;
    const deeper = line.trim();
    if (deeper.startsWith("- ")) {
      current.push(deeper.slice(2).trim());
      continue;
    }
    if (/^("?)[^"\s][^:"]*\1\s*:/.test(deeper)) {
      current.push(deeper);
      continue;
    }
    // Not a key and not a sequence item: a folded continuation of the scalar
    // above, which YAML joins with a single space.
    if (current.length === 0) current.push(deeper);
    else
      current[current.length - 1] = `${current[current.length - 1]} ${deeper}`;
  }
  return collected;
}

export function readYamlServer(
  raw: string,
  serverName: string,
): YamlServer | null {
  const lines = raw.split("\n");
  const section = findSection(lines);
  if (section === null) return null;
  const entry = entries(lines, section).find(
    (candidate) => candidate.name === serverName,
  );
  if (entry === undefined) return null;
  const found = fields(lines, entry);
  const command = found.get("command")?.[0];
  if (command === undefined) return null;
  const env: Record<string, string> = {};
  for (const pair of found.get("env") ?? []) {
    const separator = pair.indexOf(":");
    if (separator === -1) continue;
    const key = readScalar(pair.slice(0, separator).trim());
    env[key] = readScalar(pair.slice(separator + 1).trim());
  }
  return {
    command: readScalar(command),
    args: (found.get("args") ?? []).map(readScalar),
    env,
  };
}

export function putYamlServer(
  raw: string,
  serverName: string,
  server: YamlServer,
): string {
  const lines = raw.split("\n");
  const section = findSection(lines);
  if (section === null) {
    const body = render(serverName, server, "  ");
    const prefix = raw === "" || raw.endsWith("\n") ? raw : `${raw}\n`;
    return `${prefix}${SECTION}:\n${body.join("\n")}\n`;
  }
  const indent = childIndent(lines, section.body);
  const rendered = render(serverName, server, indent);
  const existing = entries(lines, section).find(
    (candidate) => candidate.name === serverName,
  );
  if (existing === undefined) {
    const at = (section.body[section.body.length - 1] ?? section.header) + 1;
    return [...lines.slice(0, at), ...rendered, ...lines.slice(at)].join("\n");
  }
  const start = existing.lines[0];
  const end = existing.lines[existing.lines.length - 1] + 1;
  return [...lines.slice(0, start), ...rendered, ...lines.slice(end)].join(
    "\n",
  );
}

export function removeYamlServer(raw: string, serverName: string): string {
  const lines = raw.split("\n");
  const section = findSection(lines);
  if (section === null) return raw;
  const all = entries(lines, section);
  const target = all.find((candidate) => candidate.name === serverName);
  if (target === undefined) return raw;
  // Taking the last entry out leaves `mcp_servers:` with no value, which YAML
  // reads as null rather than the empty map the host wrote. Remove the header
  // with it and let the caller decide whether an empty section is wanted.
  const removing =
    all.length === 1 ? [section.header, ...section.body] : target.lines;
  const drop = new Set(removing);
  return lines.filter((_line, index) => !drop.has(index)).join("\n");
}

export interface YamlMemoryProvider {
  existed: boolean;
  value: string | null;
}

/** Read only the direct `memory.provider` scalar without reserializing YAML. */
export function readYamlMemoryProvider(raw: string): YamlMemoryProvider {
  const lines = raw.split("\n");
  const section = findSection(lines, "memory");
  if (section === null) return { existed: false, value: null };
  const indent = childIndent(lines, section.body);
  for (const index of section.body) {
    const line = lines[index];
    if ((/^\s*/.exec(line)?.[0] ?? "") !== indent) continue;
    const match = /^\s*provider\s*:(.*)$/.exec(line);
    if (match) return { existed: true, value: readScalar(match[1].trim()) };
  }
  return { existed: false, value: null };
}

/** Set the direct `memory.provider` scalar while preserving all other bytes. */
export function putYamlMemoryProvider(raw: string, value: string): string {
  const lines = raw.split("\n");
  const section = findSection(lines, "memory");
  if (section === null) {
    const prefix = raw === "" || raw.endsWith("\n") ? raw : `${raw}\n`;
    return `${prefix}memory:\n  provider: ${writeScalar(value)}\n`;
  }
  const indent = childIndent(lines, section.body);
  for (const index of section.body) {
    const line = lines[index];
    if (
      (/^\s*/.exec(line)?.[0] ?? "") === indent &&
      /^\s*provider\s*:/.test(line)
    ) {
      lines[index] = `${indent}provider: ${writeScalar(value)}`;
      return lines.join("\n");
    }
  }
  lines.splice(
    section.header + 1,
    0,
    `${indent}provider: ${writeScalar(value)}`,
  );
  return lines.join("\n");
}

/** Restore the exact prior provider value, or remove the leaf if it was absent. */
export function restoreYamlMemoryProvider(
  raw: string,
  previous: YamlMemoryProvider,
): string {
  if (previous.existed) return putYamlMemoryProvider(raw, previous.value ?? "");
  const lines = raw.split("\n");
  const section = findSection(lines, "memory");
  if (section === null) return raw;
  const indent = childIndent(lines, section.body);
  const provider = section.body.find(
    (index) =>
      (/^\s*/.exec(lines[index])?.[0] ?? "") === indent &&
      /^\s*provider\s*:/.test(lines[index]),
  );
  if (provider === undefined) return raw;
  lines.splice(provider, 1);
  const refreshed = findSection(lines, "memory");
  if (refreshed?.body.every((index) => lines[index].trim() === "")) {
    lines.splice(refreshed.header, 1);
  }
  return lines.join("\n");
}

function render(
  serverName: string,
  server: YamlServer,
  indent: string,
): string[] {
  const field = `${indent}${indent}`;
  const rendered = [
    `${indent}${writeScalar(serverName)}:`,
    `${field}command: ${writeScalar(server.command)}`,
  ];
  if (server.args.length > 0) {
    rendered.push(`${field}args:`);
    for (const argument of server.args) {
      rendered.push(`${field}${indent}- ${writeScalar(argument)}`);
    }
  }
  const env = Object.entries(server.env);
  if (env.length > 0) {
    rendered.push(`${field}env:`);
    for (const [key, value] of env) {
      rendered.push(
        `${field}${indent}${writeScalar(key)}: ${writeScalar(value)}`,
      );
    }
  }
  rendered.push(`${field}enabled: true`);
  return rendered;
}

function readScalar(raw: string): string {
  if (raw.length >= 2 && raw.startsWith("'") && raw.endsWith("'")) {
    return raw.slice(1, -1).replace(/''/g, "'");
  }
  if (raw.length >= 2 && raw.startsWith('"') && raw.endsWith('"')) {
    return raw
      .slice(1, -1)
      .replace(/\\(["\\nrt])/g, (_match, character: string) => {
        if (character === "n") return "\n";
        if (character === "r") return "\r";
        if (character === "t") return "\t";
        return character;
      });
  }
  return raw;
}

/** Plain only when the value cannot be mistaken for another YAML type. */
const PLAIN_SAFE = /^[A-Za-z_/.][\w\-./@ ]*$/;
const RESERVED = /^(?:true|false|null|yes|no|on|off|~)$/i;

function writeScalar(value: string): string {
  const plain =
    value !== "" &&
    PLAIN_SAFE.test(value) &&
    !RESERVED.test(value) &&
    !value.endsWith(" ") &&
    !/^\s/.test(value);
  if (plain) return value;
  return `'${value.replace(/'/g, "''")}'`;
}
