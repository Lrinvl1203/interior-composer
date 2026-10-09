import { describe, expect, it } from "vitest";
import { backendPortFromSettings, parseSavedPorts } from "./serverConfig";

describe("portable API port", () => {
  it("defaults without settings", () => expect(backendPortFromSettings(undefined)).toBe(18877));
  it("loads root dotenv for builds", () => expect(backendPortFromSettings(undefined, "ARK_API_KEY=not-exported\nINTERIOR_BACKEND_PORT=18881")).toBe(18881));
  it("environment overrides dotenv", () => expect(backendPortFromSettings("18883", "INTERIOR_BACKEND_PORT=18881")).toBe(18883));
  it("accepts quoted dotenv values and inline comments", () => expect(backendPortFromSettings(undefined, 'INTERIOR_BACKEND_PORT="18881" # port')).toBe(18881));
  it("rejects low and oversized ports", () => { for (const value of ["1023", "65536"]) expect(() => backendPortFromSettings(value)).toThrow(); });
  it("rejects arbitrary hostnames and fractional ports", () => { for (const value of ["evil.example", "18877.5", "-1"]) expect(() => backendPortFromSettings(undefined, "INTERIOR_BACKEND_PORT="+value)).toThrow(); });
  it("uses auto-selected ports before dotenv", () => expect(backendPortFromSettings(undefined, "INTERIOR_BACKEND_PORT=18877", { backend: 18881 })).toBe(18881));
  it("environment still overrides auto-selected ports", () => expect(backendPortFromSettings("18883", "", { backend: 18881 })).toBe(18883));
  it("reads Windows UTF8-BOM port files", () => expect(backendPortFromSettings(undefined, "", parseSavedPorts('\uFEFF{"backend":18881}'))).toBe(18881));
  it("rejects malformed selected ports", () => expect(() => backendPortFromSettings(undefined, "", { backend: "other.example" })).toThrow());
});
