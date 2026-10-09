import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";

/** Parse only the non-secret API port; never export dotenv keys to the client. */
export function backendPortFromSettings(environmentValue: string | undefined, dotenvText = "", savedPorts?: unknown): number {
  let raw = environmentValue?.trim();
  if (!raw && savedPorts && typeof savedPorts === "object" && !Array.isArray(savedPorts)) {
    const value = (savedPorts as Record<string, unknown>).backend;
    if (value !== undefined) raw = String(value);
  }
  if (!raw) {
    const line = dotenvText.split(/\r?\n/).find(row => /^\s*INTERIOR_BACKEND_PORT\s*=/.test(row));
    if (line) {
      raw = line.replace(/^\s*INTERIOR_BACKEND_PORT\s*=\s*/, "").replace(/\s+#.*$/, "").trim();
      raw = raw.replace(/^(["'])(.*)\1$/, "$2");
    }
  }
  raw = raw || "18877";
  if (!/^\d+$/.test(raw)) throw new Error("Invalid INTERIOR_BACKEND_PORT");
  const port = Number(raw);
  if (!Number.isInteger(port) || port < 1024 || port > 65535) throw new Error("Invalid INTERIOR_BACKEND_PORT");
  return port;
}

export function readBackendPort(dotenvPath: string): number {
  const text = existsSync(dotenvPath) ? readFileSync(dotenvPath, "utf8") : "";
  const savedPath = join(dirname(dotenvPath), ".local", "ports.json");
  const savedPorts = existsSync(savedPath) ? parseSavedPorts(readFileSync(savedPath, "utf8")) : undefined;
  return backendPortFromSettings(process.env.INTERIOR_BACKEND_PORT, text, savedPorts);
}

export function parseSavedPorts(text: string): unknown {
  return JSON.parse(text.replace(/^\uFEFF/, ""));
}
