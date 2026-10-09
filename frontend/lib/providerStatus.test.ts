import { expect, it } from "vitest";
import { openaiKeyConfigured } from "./providerStatus";

it("allows a configured OpenAI key only", () => expect(openaiKeyConfigured({ provider: "OpenAI", configured: true })).toBe(true));
it("does not require setup to render a workspace", () => expect(openaiKeyConfigured({ provider: "OpenAI", configured: false })).toBe(false));
it("never enables a BytePlus-only account", () => expect(openaiKeyConfigured({ provider: "BytePlus", configured: true })).toBe(false));
it("does not trust truthy strings", () => expect(openaiKeyConfigured({ provider: "OpenAI", configured: "true" })).toBe(false));
it("fails closed on malformed responses", () => { for (const value of [null, [], "ready", {}, { configured: true }]) expect(openaiKeyConfigured(value)).toBe(false); });
