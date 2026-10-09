import { afterEach, describe, expect, it, vi } from "vitest";
import { ComposerDraftConflictError, composerDraftContentSignature, composerDraftWriteDecision, composerInputKey, type ComposerDraft } from "./composerDraft";

const input = {
  beforeFile: { name: "room.png", size: 1000, lastModified: 50 },
  assetIds: ["chair", "table"], finishes: { wallpaper: "cream", flooring: "oak" },
  targets: { wallpaper: "accent_wall", flooring: "floor" },
  placements: [{ asset_id: "chair", x: .2, y: .3, width: .2, height: .3 }], request: "벽만 변경", aiMode: "flash",
};
describe("rendered input identity", () => {
  it("ignores catalog ordering without relabeling a result", () => {
    expect(composerInputKey(input)).toBe(composerInputKey({ ...input, assetIds: ["table", "chair"], finishes: { flooring: "oak", wallpaper: "cream" } }));
  });
  it("marks a changed application surface, placement or source as a new draft", () => {
    const before = composerInputKey(input);
    expect(composerInputKey({ ...input, targets: { ...input.targets, wallpaper: "door" } })).not.toBe(before);
    expect(composerInputKey({ ...input, placements: [{ ...input.placements[0], x: .6 }] })).not.toBe(before);
    expect(composerInputKey({ ...input, beforeFile: { ...input.beforeFile, lastModified: 51 } })).not.toBe(before);
  });
  it("ignores stale target settings for a finish that is no longer selected", () => {
    expect(composerInputKey(input)).toBe(composerInputKey({ ...input, targets: { ...input.targets, tile: "door" } }));
  });
});

function draft(overrides: Partial<ComposerDraft> = {}): ComposerDraft {
  return {
    version: 1, updatedAt: 10, beforeFile: null, assetSource: null, selectedAssetIds: [], assetBox: null,
    extractionMode: "ai", assetName: "", assetDescription: "", assetAiMode: "flash", composeAiMode: "flash",
    finishSelections: {}, finishTargets: {}, activeFinishCategory: "wallpaper", placements: [], request: "A",
    result: null, pendingComposition: null, pendingExtraction: "", reportTitle: "", reportSpaceType: "",
    reportRecord: null, mobileStep: 0, ...overrides,
  };
}

describe("optimistic workspace save policy", () => {
  it("creates revision 1 and upgrades an unversioned legacy draft from 0", () => {
    expect(composerDraftWriteDecision(null, draft(), 0)).toEqual({ revision: 1, shouldWrite: true });
    expect(composerDraftWriteDecision(draft(), draft({ request: "B" }), 0)).toEqual({ revision: 1, shouldWrite: true });
  });

  it("prevents a stale mode-only edit from overwriting another tab's request", () => {
    const latest = draft({ request: "B", storageRevision: 2 });
    const oldTab = draft({ request: "A", composeAiMode: "premium", storageRevision: 1 });
    expect(() => composerDraftWriteDecision(latest, oldTab, 1)).toThrow(ComposerDraftConflictError);
    try { composerDraftWriteDecision(latest, oldTab, 1); }
    catch (error) {
      expect(error).toMatchObject({ expectedRevision: 1, currentRevision: 2 });
    }
    expect(latest.request).toBe("B");
    expect(latest.storageRevision).toBe(2);
  });

  it("allows stale identical content without a write or revision increment", () => {
    const latest = draft({ storageRevision: 5 });
    expect(composerDraftWriteDecision(latest, draft({ updatedAt: 999, storageRevision: 1 }), 1))
      .toEqual({ revision: 5, shouldWrite: false });
    expect(latest.updatedAt).toBe(10);
  });

  it("increments the current revision for a valid content update", () => {
    expect(composerDraftWriteDecision(draft({ storageRevision: 3 }), draft({ request: "B" }), 3))
      .toEqual({ revision: 4, shouldWrite: true });
  });

  it("rejects an absent record when the caller expects an existing revision", () => {
    expect(() => composerDraftWriteDecision(null, draft(), 4)).toThrow(ComposerDraftConflictError);
  });

  it.each([-1, 1.5, Number.NaN, Number.POSITIVE_INFINITY])("rejects invalid expected revision %s", revision => {
    expect(() => composerDraftWriteDecision(draft(), draft(), revision)).toThrow();
  });

  it("does not repair or overwrite malformed stored data silently", () => {
    expect(() => composerDraftWriteDecision(draft({ storageRevision: -1 }), draft(), 0)).toThrow();
    expect(() => composerDraftWriteDecision({ version: 2 } as unknown as ComposerDraft, draft(), 0)).toThrow();
  });
});

describe("complete draft content identity", () => {
  it("stabilizes object key order but excludes only top-level storage timestamps/revisions", () => {
    const a = draft({ finishSelections: { wallpaper: "cream", flooring: "oak" } });
    const b = draft({ updatedAt: 99, storageRevision: 17, finishSelections: { flooring: "oak", wallpaper: "cream" } });
    expect(composerDraftContentSignature(a)).toBe(composerDraftContentSignature(b));
  });

  it.each([
    { request: "B" }, { composeAiMode: "premium" }, { selectedAssetIds: ["table"] },
    { finishTargets: { wallpaper: "door" } }, { pendingComposition: { jobId: "pending", inputKey: "input" } },
    { pendingExtraction: "extracting" }, { reportTitle: "New proposal" }, { mobileStep: 2 },
    { rejectedResult: { jobId: "rejected", url: "/after.png", messages: ["duplicate"] } },
    { result: { jobId: "accepted", url: "/accepted.png" } },
    { verifiedBackup: { result: { jobId: "backup" }, report: null } },
    { reportRecord: { report_id: "report", version: 1, share_url: "/r/1", pdf_url: "/r.pdf", versions: [] } },
  ])("includes input, result, pending and report workspace changes: %j", change => {
    expect(composerDraftContentSignature(draft(change as Partial<ComposerDraft>))).not.toBe(composerDraftContentSignature(draft()));
  });

  it("includes File metadata and recognizes equivalent restored File objects", () => {
    const first = new File(["abc"], "before.png", { type: "image/png", lastModified: 123 });
    const restored = new File(["abc"], "before.png", { type: "image/png", lastModified: 123 });
    const changed = new File(["abc"], "before.png", { type: "image/png", lastModified: 124 });
    expect(composerDraftContentSignature(draft({ beforeFile: first }))).toBe(composerDraftContentSignature(draft({ beforeFile: restored })));
    expect(composerDraftContentSignature(draft({ beforeFile: first }))).not.toBe(composerDraftContentSignature(draft({ beforeFile: changed })));
  });

  it("retains nested report timestamps as real report content", () => {
    const report = { report_id: "r", version: 1, share_url: "/r", pdf_url: "/r.pdf", versions: [{ report_id: "r", version: 1, created_at: "2026-10-09" }] };
    const modified = { ...report, versions: [{ ...report.versions[0], created_at: "2026-10-10" }] };
    expect(composerDraftContentSignature(draft({ reportRecord: report }))).not.toBe(composerDraftContentSignature(draft({ reportRecord: modified })));
  });
});

/** Minimal transactional fake: readwrite transactions serialize and publish only on commit.
 * put must be synchronous within get.onsuccess, so an await inside that callback fails.
 * No real browser database, application data, or additional dependency is involved.
 */
function indexedDbFake(initial: ComposerDraft | null = null) {
  const state = { value: initial, writes: 0, keys: [] as string[], modes: [] as string[] };
  const queued: Array<() => void> = [];
  let busy = false;
  const runNext = () => { if (!busy && queued.length) { busy = true; queueMicrotask(queued.shift()!); } };
  const database = {
    objectStoreNames: { contains: () => true }, close: () => {},
    transaction(_store: string, mode: string) {
      state.modes.push(mode);
      let pending = state.value;
      let readRequest: { result?: ComposerDraft; onsuccess?: () => void; onerror?: () => void };
      let insideCallback = false;
      let aborted = false;
      let didPut = false;
      const transaction = {
        error: null, oncomplete: undefined as undefined | (() => void), onabort: undefined as undefined | (() => void), onerror: undefined as undefined | (() => void),
        abort() { aborted = true; },
        objectStore() { return {
          get(key: string) { state.keys.push(key); readRequest = {}; return readRequest; },
          put(value: ComposerDraft, key: string) {
            if (!insideCallback) throw new Error("IndexedDB transaction is inactive");
            state.keys.push(key); pending = value; didPut = true;
          },
        }; },
      };
      queued.push(() => {
        pending = state.value;
        readRequest.result = state.value ?? undefined;
        insideCallback = true;
        readRequest.onsuccess?.();
        insideCallback = false;
        queueMicrotask(() => {
          if (aborted) transaction.onabort?.();
          else {
            if (didPut) { state.value = pending; state.writes += 1; }
            transaction.oncomplete?.();
          }
          busy = false;
          runNext();
        });
      });
      runNext();
      return transaction;
    },
  };
  const factory = {
    open() {
      const request = { result: database, onsuccess: undefined as undefined | (() => void) };
      queueMicrotask(() => request.onsuccess?.());
      return request;
    },
  };
  return { factory, state };
}

describe("IndexedDB atomic optimistic save", () => {
  afterEach(() => { vi.unstubAllGlobals(); vi.resetModules(); });

  it("normalizes a legacy read revision without writing application data", async () => {
    const mock = indexedDbFake(draft());
    vi.stubGlobal("indexedDB", mock.factory);
    vi.resetModules();
    const storage = await import("./composerDraft");
    expect((await storage.readComposerDraft())?.storageRevision).toBe(0);
    expect(mock.state.writes).toBe(0);
    expect(mock.state.value?.storageRevision).toBeUndefined();
  });

  it("reproduces two-tab request/mode conflict and preserves the newer record", async () => {
    const mock = indexedDbFake(draft({ storageRevision: 1 }));
    vi.stubGlobal("indexedDB", mock.factory);
    vi.resetModules();
    const storage = await import("./composerDraft");
    const staleA = (await storage.readComposerDraft())!;
    const tabB = (await storage.readComposerDraft())!;
    expect(await storage.writeComposerDraft({ ...tabB, request: "B" }, 1)).toBe(2);
    await expect(storage.writeComposerDraft({ ...staleA, composeAiMode: "premium" }, 1)).rejects.toBeInstanceOf(storage.ComposerDraftConflictError);
    expect((await storage.readComposerDraft())?.request).toBe("B");
    expect(mock.state.value?.composeAiMode).toBe("flash");
    expect(mock.state.value?.storageRevision).toBe(2);
    expect(mock.state.writes).toBe(1);
    expect(mock.state.keys.every(key => key === storage.COMPOSER_DRAFT_KEY)).toBe(true);
  });

  it("serializes concurrent read/compare/write transactions so only one divergent save wins", async () => {
    const mock = indexedDbFake(draft({ storageRevision: 1 }));
    vi.stubGlobal("indexedDB", mock.factory);
    vi.resetModules();
    const storage = await import("./composerDraft");
    const outcomes = await Promise.allSettled([
      storage.writeComposerDraft(draft({ request: "B" }), 1),
      storage.writeComposerDraft(draft({ request: "C" }), 1),
    ]);
    expect(outcomes.map(outcome => outcome.status)).toEqual(["fulfilled", "rejected"]);
    expect(mock.state.value?.request).toBe("B");
    expect(mock.state.value?.storageRevision).toBe(2);
    expect(mock.state.writes).toBe(1);
    expect(mock.state.modes).toEqual(["readwrite", "readwrite"]);
  });

  it("returns the current revision for stale identical content without touching updatedAt", async () => {
    const mock = indexedDbFake(draft({ storageRevision: 4, updatedAt: 123 }));
    vi.stubGlobal("indexedDB", mock.factory);
    vi.resetModules();
    const storage = await import("./composerDraft");
    expect(await storage.writeComposerDraft(draft({ updatedAt: 999 }), 1)).toBe(4);
    expect(mock.state.writes).toBe(0);
    expect(mock.state.value?.updatedAt).toBe(123);
  });
});
