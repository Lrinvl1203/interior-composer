/** Presence of one configured OpenAI key, not an account/billing verification. */
export function openaiKeyConfigured(value: unknown): boolean {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const status = value as Record<string, unknown>;
  return status.provider === "OpenAI" && status.configured === true;
}
