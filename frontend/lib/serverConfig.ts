import { existsSync, readFileSync } from "node:fs";

/** Parse only the non-secret API port; never export dotenv keys to the client. */
export function backendPortFromSettings(environmentValue: string | undefined, dotenvText = ""): number {
  let raw = environmentValue?.trim();
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
  return backendPortFromSettings(process.env.INTERIOR_BACKEND_PORT, text);
}
