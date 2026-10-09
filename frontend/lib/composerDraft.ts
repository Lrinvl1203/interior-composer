export type ComposerPlacement = { asset_id: string; x: number; y: number; width: number; height: number };
export type CompositionReview = {
  status: "passed" | "failed" | "unverified" | "approved";
  method?: string;
  issues?: Array<{ code?: string; message?: string }>;
};
export type CompositionSnapshot = {
  assets: Array<{ asset_id: string; name?: string; category?: string }>;
  finishes: Array<{ id: string; name?: string; category?: string; target?: string }>;
  placements: ComposerPlacement[];
  finish_targets: Record<string, string>;
  user_request: string;
  ai_mode: string;
};
export type SavedComposerResult = {
  jobId: string;
  url: string;
  sourceUrl: string;
  inputKey: string;
  frameAligned: boolean;
  unchangedPixelRatio: number | null;
  snapshot: CompositionSnapshot | null;
  review: CompositionReview | null;
  readyForReport: boolean;
  revision: string | number;
  sha256: string;
  warnings: string[];
  alignmentMethod?: string;
  providerCalls?: Array<{ model?: string; usage_available?: boolean; usage?: Record<string, unknown> }>;
  qualityCalls?: Array<{ model?: string; provider_invoked?: boolean; usage?: Record<string, unknown> }>;
};
export type SavedComposerReport = {
  report_id: string;
  version: number;
  share_url: string;
  pdf_url: string;
  versions: Array<{ report_id: string; version: number; created_at: string }>;
};
export type ComposerDraft = {
  version: 1;
  updatedAt: number;
  storageRevision?: number;
  beforeFile: File | null;
  assetSource: File | null;
  selectedAssetIds: string[];
  assetBox: [number, number, number, number] | null;
  extractionMode: "ai" | "sam";
  assetName: string;
  assetDescription: string;
  assetAiMode: "flash" | "premium";
  composeAiMode: "flash" | "premium";
  finishSelections: Record<string, string>;
  finishTargets: Record<string, string>;
  activeFinishCategory: string;
  placements: ComposerPlacement[];
  request: string;
  result: SavedComposerResult | null;
  pendingComposition: { jobId: string; inputKey: string } | null;
  pendingExtraction: string;
  reportTitle: string;
  reportSpaceType: string;
  reportRecord: SavedComposerReport | null;
  mobileStep: number;
  rejectedResult?: { jobId: string; url: string; messages: string[] } | null;
  verifiedBackup?: { result: SavedComposerResult; report: SavedComposerReport | null } | null;
};

const DB_NAME = "morrow-interior-composer";
const STORE_NAME = "drafts";
export const COMPOSER_DRAFT_KEY = "current-v1";
let opening: Promise<IDBDatabase> | null = null;

export class ComposerDraftConflictError extends Error {
  constructor(public readonly expectedRevision: number, public readonly currentRevision: number) {
    super("다른 Composer 탭에서 이 작업을 변경했습니다. 최신 저장본을 불러온 뒤 다시 수정해 주세요.");
    this.name = "ComposerDraftConflictError";
  }
}

function draftRevision(draft: ComposerDraft | null): number {
  const revision = draft?.storageRevision ?? 0;
  if (!Number.isSafeInteger(revision) || revision < 0) {
    throw new Error("저장된 작업 리비전이 올바르지 않습니다. 기존 저장본은 보존했습니다.");
  }
  return revision;
}

function validateDraft(value: ComposerDraft): void {
  if (!value || value.version !== 1 || !Array.isArray(value.selectedAssetIds) || !Array.isArray(value.placements)) {
    throw new Error("저장된 작업 형식이 올바르지 않습니다. 기존 저장본은 보존했습니다.");
  }
  draftRevision(value);
}

/** Compare the complete workspace, not only generation inputs. File bytes stay in IDB. */
export function composerDraftContentSignature(draft: ComposerDraft): string {
  const canonical = (value: unknown): unknown => {
    if (typeof Blob !== "undefined" && value instanceof Blob) {
      const file = value as File;
      return { $type: typeof file.name === "string" ? "File" : "Blob", size: value.size, type: value.type,
        ...(typeof file.name === "string" ? { name: file.name, lastModified: file.lastModified } : {}) };
    }
    if (Array.isArray(value)) return value.map(canonical);
    if (value && typeof value === "object") {
      return Object.fromEntries(Object.keys(value).sort().map(key => [key, canonical((value as Record<string, unknown>)[key])]));
    }
    return value;
  };
  // Only storage bookkeeping is excluded; nested report/result timestamps are content.
  const { updatedAt: _updatedAt, storageRevision: _storageRevision, ...content } = draft;
  return JSON.stringify(canonical(content));
}

/** Pure policy used inside the same IDB readwrite transaction as the eventual put. */
export function composerDraftWriteDecision(current: ComposerDraft | null, draft: ComposerDraft, expectedRevision: number): { revision: number; shouldWrite: boolean } {
  validateDraft(draft);
  if (current) validateDraft(current);
  if (!Number.isSafeInteger(expectedRevision) || expectedRevision < 0) throw new Error("작업 저장 리비전이 올바르지 않습니다.");
  const revision = draftRevision(current);
  if (current && composerDraftContentSignature(current) === composerDraftContentSignature(draft)) {
    return { revision, shouldWrite: false };
  }
  if (revision !== expectedRevision) throw new ComposerDraftConflictError(expectedRevision, revision);
  if (revision === Number.MAX_SAFE_INTEGER) throw new Error("작업 저장 리비전 한도를 초과했습니다. 기존 저장본은 보존했습니다.");
  return { revision: revision + 1, shouldWrite: true };
}

function database(): Promise<IDBDatabase> {
  if (!opening) opening = new Promise<IDBDatabase>((resolve, reject) => {
    if (typeof indexedDB === "undefined") { reject(new Error("이 브라우저에서는 작업 저장을 사용할 수 없습니다.")); return; }
    const request = indexedDB.open(DB_NAME, 1);
    request.onupgradeneeded = () => { if (!request.result.objectStoreNames.contains(STORE_NAME)) request.result.createObjectStore(STORE_NAME); };
    request.onerror = () => reject(request.error || new Error("작업 저장소를 열지 못했습니다."));
    request.onblocked = () => reject(new Error("다른 Composer 탭을 닫고 작업 저장을 다시 시도해 주세요."));
    request.onsuccess = () => {
      request.result.onversionchange = () => { request.result.close(); opening = null; };
      resolve(request.result);
    };
  }).catch(error => { opening = null; throw error; });
  return opening;
}

export async function readComposerDraft(): Promise<ComposerDraft | null> {
  const db = await database();
  return new Promise((resolve, reject) => {
    const transaction = db.transaction(STORE_NAME, "readonly");
    const request = transaction.objectStore(STORE_NAME).get(COMPOSER_DRAFT_KEY);
    request.onerror = () => reject(request.error);
    request.onsuccess = () => {
      const value = request.result as ComposerDraft | undefined;
      if (!value) { resolve(null); return; }
      try { validateDraft(value); resolve({ ...value, storageRevision: draftRevision(value) }); }
      catch (error) { reject(error); }
    };
  });
}

export async function writeComposerDraft(draft: ComposerDraft, expectedRevision: number): Promise<number> {
  const db = await database();
  return new Promise((resolve, reject) => {
    const transaction = db.transaction(STORE_NAME, "readwrite");
    const store = transaction.objectStore(STORE_NAME);
    const request = store.get(COMPOSER_DRAFT_KEY);
    let revision = expectedRevision;
    let failure: unknown = null;
    request.onsuccess = () => {
      try {
        const decision = composerDraftWriteDecision(request.result ?? null, draft, expectedRevision);
        revision = decision.revision;
        // Keep get, conflict check and put in this callback/transaction: no await.
        if (decision.shouldWrite) store.put({ ...draft, storageRevision: revision }, COMPOSER_DRAFT_KEY);
      } catch (error) {
        failure = error;
        transaction.abort();
      }
    };
    request.onerror = () => { failure = request.error; };
    transaction.oncomplete = () => resolve(revision);
    transaction.onabort = () => reject(failure || transaction.error || new Error("작업 저장에 실패했습니다. 브라우저 저장 공간을 확인해 주세요."));
    transaction.onerror = () => reject(failure || transaction.error || new Error("작업 저장에 실패했습니다."));
  });
}

/** Stable input identity: metadata changes must never relabel an older image. */
export function composerInputKey(input: {
  beforeFile: Pick<File, "name" | "size" | "lastModified"> | null;
  assetIds: string[];
  finishes: Record<string, string>;
  targets: Record<string, string>;
  placements: ComposerPlacement[];
  request: string;
  aiMode: string;
}): string {
  const sorted = (values: Record<string, string>) => Object.keys(values).sort().map(key => [key, values[key]]);
  return JSON.stringify({
    source: input.beforeFile ? [input.beforeFile.name, input.beforeFile.size, input.beforeFile.lastModified] : null,
    assets: [...input.assetIds].sort(),
    finishes: sorted(input.finishes),
    targets: sorted(Object.fromEntries(Object.keys(input.finishes).map(key => [key, input.targets[key] || "auto"]))),
    placements: [...input.placements].sort((a, b) => a.asset_id.localeCompare(b.asset_id)),
    request: input.request,
    aiMode: input.aiMode,
  });
}
