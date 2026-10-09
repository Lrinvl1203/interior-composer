"use client";

import { DragEvent, PointerEvent as ReactPointerEvent, useEffect, useMemo, useRef, useState } from "react";
import {
  Check,
  Copy,
  Download,
  ExternalLink,
  FileText,
  Image as ImageIcon,
  Layers3,
  LoaderCircle,
  Move,
  Scissors,
  ShieldCheck,
  Sparkles,
  Upload,
  Wand2,
  X,
} from "lucide-react";

import BeforeAfterSlider from "@/components/journey/BeforeAfterSlider";
import { ComposerDraftConflictError, composerInputKey, readComposerDraft, writeComposerDraft, type ComposerDraft, type CompositionReview, type CompositionSnapshot, type SavedComposerResult } from "@/lib/composerDraft";
import styles from "./InteriorComposer.module.css";

type Asset = {
  asset_id: string;
  asset_name: string;
  category: string;
  url: string;
  provenance?: string;
};

type FinishCategory = { key: string; label: string; note?: string };
type FinishItem = {
  id: string;
  category: string;
  name: string;
  tone?: string;
  finish?: string;
  image_url?: string | null;
};
type FinishCatalog = { categories: FinishCategory[]; items: FinishItem[] };
type Placement = { asset_id: string; x: number; y: number; width: number; height: number };
type AiMode = "flash" | "premium";
type ExtractionMode = "ai" | "sam";
type ComposerReport = {
  report_id: string;
  version: number;
  share_url: string;
  pdf_url: string;
  versions: Array<{ report_id: string; version: number; created_at: string }>;
};

const CATEGORY_LABELS: Record<string, string> = {
  wallpaper: "벽지",
  flooring: "장판·바닥재",
  tile: "타일",
  film: "필름",
};

const DEFAULT_TARGETS: Record<string, string> = {
  wallpaper: "all_walls",
  flooring: "floor",
  tile: "backsplash",
  film: "fixed_joinery",
};

const TARGET_OPTIONS = [
  ["auto", "AI 자동 판단"],
  ["all_walls", "보이는 전체 벽"],
  ["accent_wall", "포인트 벽"],
  ["floor", "바닥"],
  ["ceiling", "천장"],
  ["door", "문"],
  ["fixed_joinery", "고정 가구·문짝"],
  ["backsplash", "주방·욕실 벽면"],
] as const;

function errorMessage(payload: unknown, fallback: string) {
  if (payload && typeof payload === "object" && "detail" in payload && typeof payload.detail === "string") {
    return payload.detail;
  }
  return fallback;
}

function clamp(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value));
}

export default function InteriorComposer() {
  const [assets, setAssets] = useState<Asset[]>([]);
  const [selectedAssetIds, setSelectedAssetIds] = useState<string[]>([]);
  const [assetSource, setAssetSource] = useState<File | null>(null);
  const [assetSourceUrl, setAssetSourceUrl] = useState("");
  const [assetBox, setAssetBox] = useState<[number, number, number, number] | null>(null);
  const [assetDragStart, setAssetDragStart] = useState<[number, number] | null>(null);
  const [extractionMode, setExtractionMode] = useState<ExtractionMode>("ai");
  const [assetName, setAssetName] = useState("");
  const [assetDescription, setAssetDescription] = useState("");
  const [extracting, setExtracting] = useState(false);
  const [assetMessage, setAssetMessage] = useState("AI 추출이 기본입니다. 박스로 대상을 고르고 어떤 에셋인지 설명해 주세요.");

  const [catalog, setCatalog] = useState<FinishCatalog>({ categories: [], items: [] });
  const [finishSelections, setFinishSelections] = useState<Record<string, string>>({});
  const [activeFinishCategory, setActiveFinishCategory] = useState("");
  const [finishTargets, setFinishTargets] = useState<Record<string, string>>({});

  const [beforeFile, setBeforeFile] = useState<File | null>(null);
  const [beforeUrl, setBeforeUrl] = useState("");
  const [placements, setPlacements] = useState<Placement[]>([]);
  const [request, setRequest] = useState("");
  const [assetAiMode, setAssetAiMode] = useState<AiMode>("flash");
  const [composeAiMode, setComposeAiMode] = useState<AiMode>("premium");
  const [working, setWorking] = useState(false);
  const [status, setStatus] = useState("STEP 1과 STEP 2를 완료하면 부분 합성을 시작할 수 있습니다.");
  const [savedResult, setSavedResult] = useState<SavedComposerResult | null>(null);
  const [rejectedResult, setRejectedResult] = useState<ComposerDraft["rejectedResult"]>(null);
  const [verifiedBackup, setVerifiedBackup] = useState<ComposerDraft["verifiedBackup"]>(null);
  const savedResultRef = useRef<SavedComposerResult | null>(null);
  savedResultRef.current = savedResult;
  const resultUrl = savedResult?.url || "";
  const frameAligned = savedResult?.frameAligned || false;
  const unchangedPixelRatio = savedResult?.unchangedPixelRatio ?? null;
  const [showComposeConfirm, setShowComposeConfirm] = useState(false);
  const composeJobId = savedResult?.jobId || "";
  const [pendingComposition, setPendingComposition] = useState<ComposerDraft["pendingComposition"]>(null);
  const [pendingExtraction, setPendingExtraction] = useState("");
  const [pollEpoch, setPollEpoch] = useState(0);
  const [draftLoaded, setDraftLoaded] = useState(false);
  const [storageReady, setStorageReady] = useState(false);
  const [saveConflict, setSaveConflict] = useState(false);
  const [saveStatus, setSaveStatus] = useState("저장된 작업을 확인하고 있습니다.");
  const [mobileStep, setMobileStep] = useState(1);
  const [assetLimit, setAssetLimit] = useState(8);
  const [finishLimit, setFinishLimit] = useState(8);
  const [placementAssetId, setPlacementAssetId] = useState("");
  const [reviewChecks, setReviewChecks] = useState({ inventory: false, structure: false, finishes: false });
  const [reviewWorking, setReviewWorking] = useState(false);
  const [reportTitle, setReportTitle] = useState("Before / After 인테리어 상담 보고서");
  const [reportSpaceType, setReportSpaceType] = useState("거실");
  const [reportWorking, setReportWorking] = useState(false);
  const [reportMessage, setReportMessage] = useState("After가 완성되면 상담 보고서를 저장할 수 있습니다.");
  const [reportRecord, setReportRecord] = useState<ComposerReport | null>(null);
  const reportRecordRef = useRef<ComposerReport | null>(null);
  reportRecordRef.current = reportRecord;
  const sourceImageRef = useRef<HTMLImageElement>(null);
  const confirmDialogRef = useRef<HTMLDialogElement>(null);
  const confirmTriggerRef = useRef<HTMLElement | null>(null);
  const beforeCanvasRef = useRef<HTMLDivElement>(null);
  const saveQueue = useRef<Promise<void>>(Promise.resolve());
  const saveSequence = useRef(0);
  const storageRevisionRef = useRef(0);
  const storageWriteBlockedRef = useRef(false);

  useEffect(() => {
    let active = true;
    void readComposerDraft().then((draft) => {
      if (!active) return;
      storageRevisionRef.current = draft?.storageRevision || 0;
      if (draft) {
        setSelectedAssetIds(draft.selectedAssetIds);
        setAssetSource(draft.assetSource);
        setAssetSourceUrl(draft.assetSource ? URL.createObjectURL(draft.assetSource) : "");
        setAssetBox(draft.assetBox);
        setExtractionMode(draft.extractionMode);
        setAssetName(draft.assetName);
        setAssetDescription(draft.assetDescription);
        setAssetAiMode(draft.assetAiMode);
        setComposeAiMode(draft.composeAiMode);
        setFinishSelections(draft.finishSelections);
        setFinishTargets(draft.finishTargets);
        setActiveFinishCategory(draft.activeFinishCategory);
        setBeforeFile(draft.beforeFile);
        setBeforeUrl(draft.beforeFile ? URL.createObjectURL(draft.beforeFile) : "");
        setPlacements(draft.placements);
        setRequest(draft.request);
        setSavedResult(draft.result);
        setRejectedResult(draft.rejectedResult || null);
        setVerifiedBackup(draft.verifiedBackup || null);
        setPendingComposition(draft.pendingComposition);
        setPendingExtraction(draft.pendingExtraction || "");
        setReportTitle(draft.reportTitle);
        setReportSpaceType(draft.reportSpaceType);
        setReportRecord(draft.reportRecord);
        setMobileStep(draft.mobileStep || 1);
        setStatus(draft.pendingComposition ? "진행 중인 합성 작업을 다시 확인합니다." : draft.result ? "저장된 결과와 선택 내용을 복원했습니다." : "저장된 사진과 선택 내용을 복원했습니다.");
      }
      setStorageReady(true);
      setSaveStatus(draft ? "저장된 작업을 복원했습니다." : "이 브라우저에 작업을 자동 저장합니다.");
    }).catch((error) => {
      if (active) setSaveStatus(error instanceof Error ? error.message : "작업 저장소를 열지 못했습니다.");
    }).finally(() => { if (active) setDraftLoaded(true); });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (!draftLoaded || !storageReady) return;
    const draft: ComposerDraft = {
      version: 1, updatedAt: Date.now(), beforeFile, assetSource, selectedAssetIds, assetBox,
      extractionMode, assetName, assetDescription, assetAiMode, composeAiMode, finishSelections,
      finishTargets, activeFinishCategory, placements, request, result: savedResult,
      pendingComposition, pendingExtraction, reportTitle, reportSpaceType, reportRecord, mobileStep, rejectedResult, verifiedBackup,
    };
    const sequence = ++saveSequence.current;
    setSaveStatus("저장 중…");
    saveQueue.current = saveQueue.current.catch(() => {}).then(() => {
      if (storageWriteBlockedRef.current) return null;
      return writeComposerDraft(draft, storageRevisionRef.current);
    }).then((revision) => {
      if (revision === null) return;
      storageRevisionRef.current = revision;
      if (sequence === saveSequence.current && !storageWriteBlockedRef.current) setSaveStatus("이 브라우저에 저장됨");
    }).catch((error) => {
      if (error instanceof ComposerDraftConflictError) {
        storageWriteBlockedRef.current = true;
        setSaveConflict(true);
        setStorageReady(false);
        setSaveStatus("다른 탭에서 더 최신 작업을 저장했습니다. 덮어쓰지 않고 자동 저장을 멈췄습니다.");
        return;
      }
      if (sequence === saveSequence.current) setSaveStatus(error instanceof Error ? error.message : "자동 저장에 실패했습니다. 저장 공간을 확인해 주세요.");
    });
  }, [draftLoaded, storageReady, beforeFile, assetSource, selectedAssetIds, assetBox, extractionMode, assetName, assetDescription, assetAiMode, composeAiMode, finishSelections, finishTargets, activeFinishCategory, placements, request, savedResult, pendingComposition, pendingExtraction, reportTitle, reportSpaceType, reportRecord, mobileStep, rejectedResult, verifiedBackup]);

  useEffect(() => {
    if (showComposeConfirm && confirmDialogRef.current && !confirmDialogRef.current.open) confirmDialogRef.current.showModal();
  }, [showComposeConfirm]);

  useEffect(() => {
    const html = document.documentElement;
    const body = document.body;
    const previous = {
      htmlOverflowY: html.style.overflowY,
      htmlHeight: html.style.height,
      bodyOverflowY: body.style.overflowY,
      bodyHeight: body.style.height,
    };
    html.style.overflowY = "auto";
    html.style.height = "auto";
    body.style.overflowY = "auto";
    body.style.height = "auto";
    return () => {
      html.style.overflowY = previous.htmlOverflowY;
      html.style.height = previous.htmlHeight;
      body.style.overflowY = previous.bodyOverflowY;
      body.style.height = previous.bodyHeight;
    };
  }, []);

  const loadAssets = async () => {
    const response = await fetch("/api/assets");
    const payload: unknown = await response.json().catch(() => []);
    if (!response.ok || !Array.isArray(payload)) throw new Error(errorMessage(payload, "에셋 라이브러리를 불러오지 못했습니다."));
    setAssets(payload.filter((item): item is Asset => Boolean(item && typeof item === "object" && "asset_id" in item && "url" in item)));
  };

  useEffect(() => {
    void Promise.all([
      loadAssets().catch((error) => setAssetMessage(error instanceof Error ? error.message : "에셋 라이브러리를 불러오지 못했습니다.")),
      fetch("/api/finish-library").then(async (response) => {
        const payload: unknown = await response.json().catch(() => ({}));
        if (!response.ok || !payload || typeof payload !== "object") throw new Error(errorMessage(payload, "마감재 라이브러리를 불러오지 못했습니다."));
        const value = payload as FinishCatalog;
        setCatalog({ categories: Array.isArray(value.categories) ? value.categories : [], items: Array.isArray(value.items) ? value.items : [] });
        setActiveFinishCategory((current) => current || value.categories?.[0]?.key || "");
      }).catch((error) => setStatus(error instanceof Error ? error.message : "마감재 라이브러리를 불러오지 못했습니다.")),
    ]);
  }, []);

  useEffect(() => () => {
    if (assetSourceUrl) URL.revokeObjectURL(assetSourceUrl);
  }, [assetSourceUrl]);

  useEffect(() => () => {
    if (beforeUrl) URL.revokeObjectURL(beforeUrl);
  }, [beforeUrl]);

  const selectedAssets = useMemo(
    () => selectedAssetIds.map((id) => assets.find((asset) => asset.asset_id === id)).filter((asset): asset is Asset => Boolean(asset)),
    [assets, selectedAssetIds],
  );
  const selectedFinishes = useMemo(
    () => Object.entries(finishSelections).map(([category, id]) => catalog.items.find((item) => item.id === id && item.category === category)).filter((item): item is FinishItem => Boolean(item)),
    [catalog.items, finishSelections],
  );
  const visibleFinishItems = catalog.items.filter((item) => item.category === activeFinishCategory);
  const currentInputKey = composerInputKey({
    beforeFile, assetIds: selectedAssetIds, finishes: finishSelections,
    targets: Object.fromEntries(Object.keys(finishSelections).map(category => [category, finishTargets[category] || DEFAULT_TARGETS[category] || "auto"])),
    placements, request, aiMode: composeAiMode,
  });
  const resultIsStale = Boolean(savedResult && savedResult.inputKey !== currentInputKey);
  const readyForReport = Boolean(savedResult?.readyForReport);
  const qualityStatus = savedResult?.review?.status || "unverified";

  const adoptCompletedJob = (value: Record<string, unknown>, inputKey: string) => {
    const pixelLock = value.pixel_lock && typeof value.pixel_lock === "object" ? value.pixel_lock as Record<string, unknown> : {};
    setSavedResult({
      jobId: String(value.job_id || ""), url: String(value.result_url || ""), sourceUrl: String(value.source_url || ""), inputKey,
      frameAligned: Boolean(value.frame_aligned), unchangedPixelRatio: typeof pixelLock.unchanged_pixel_ratio === "number" ? pixelLock.unchanged_pixel_ratio : null,
      alignmentMethod: typeof pixelLock.alignment_method === "string" ? pixelLock.alignment_method : undefined,
      snapshot: value.composition_snapshot && typeof value.composition_snapshot === "object" ? value.composition_snapshot as CompositionSnapshot : null,
      review: value.quality_review && typeof value.quality_review === "object" ? value.quality_review as CompositionReview : null,
      readyForReport: Boolean(value.ready_for_report),
      revision: typeof value.result_revision === "number" ? value.result_revision : String(value.result_revision || ""),
      sha256: String(value.result_sha256 || ""), warnings: Array.isArray(value.warnings) ? value.warnings.map(String) : [],
      providerCalls: Array.isArray(value.provider_calls) ? value.provider_calls : [],
      qualityCalls: Array.isArray(value.quality_calls) ? value.quality_calls : [],
    });
    setReviewChecks({ inventory: false, structure: false, finishes: false });
  };

  useEffect(() => {
    if (!draftLoaded || !pendingComposition) return;
    let active = true;
    setWorking(true);
    void (async () => {
      try {
        for (let index = 0; index < 800 && active; index += 1) {
          const response = await fetch(`/api/jobs/${pendingComposition.jobId}`, { cache: "no-store" });
          const payload: unknown = await response.json().catch(() => ({}));
          if (!active) return;
          if (!response.ok || !payload || typeof payload !== "object") throw new Error(errorMessage(payload, "합성 상태를 확인하지 못했습니다. 상태 확인을 다시 시도해 주세요."));
          const value = payload as Record<string, unknown>;
          setStatus(String(value.stage_label || "부분 합성 중"));
          if (value.status === "done") {
            if (!value.result_url) throw new Error("완료 결과의 이미지 주소를 찾지 못했습니다.");
            const review = value.quality_review && typeof value.quality_review === "object" ? value.quality_review as CompositionReview : null;
            if (savedResultRef.current?.readyForReport && !value.ready_for_report) setVerifiedBackup({ result: savedResultRef.current, report: reportRecordRef.current });
            else if (value.ready_for_report) setVerifiedBackup(null);
            if (savedResultRef.current?.readyForReport && (review?.status === "failed" || !value.frame_aligned)) {
              setRejectedResult({ jobId: String(value.job_id || pendingComposition.jobId), url: String(value.result_url), messages: review?.issues?.map(issue => issue.message || "검수 불일치가 발견됐습니다.") || ["좌표 정렬 검사를 통과하지 못했습니다."] });
              setPendingComposition(null);
              setStatus("새 결과가 검수를 통과하지 못해 이전 검수 완료본과 보고서를 유지했습니다.");
              return;
            }
            setRejectedResult(null);
            adoptCompletedJob(value, pendingComposition.inputKey);
            setReportRecord(null);
            setPendingComposition(null);
            setStatus(value.ready_for_report ? "합성과 검수가 완료되었습니다. 보고서를 저장할 수 있습니다." : "이미지 생성 완료 · 아래 검수 상태를 확인해 주세요.");
            return;
          }
          if (["error", "stale"].includes(String(value.status))) {
            setPendingComposition(null);
            throw new Error(String(value.error || "합성 작업이 중단되었습니다."));
          }
          await new Promise(resolve => window.setTimeout(resolve, 1500));
        }
        if (active) throw new Error("작업 확인 시간이 길어지고 있습니다. 상태 확인을 다시 시도해 주세요.");
      } catch (error) {
        if (active) setStatus(`${error instanceof Error ? error.message : "서버 연결을 확인해 주세요."} 이전 결과와 입력은 보존했습니다.`);
      } finally { if (active) setWorking(false); }
    })();
    return () => { active = false; };
    // A new pending ID or an explicit retry restarts observation, never generation.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draftLoaded, pendingComposition?.jobId, pollEpoch]);

  useEffect(() => {
    if (!draftLoaded || !pendingExtraction) return;
    let active = true;
    setExtracting(true);
    void (async () => {
      try {
        for (let index = 0; index < 600 && active; index += 1) {
          const response = await fetch(`/api/jobs/${pendingExtraction}`, { cache: "no-store" });
          const value = await response.json() as Record<string, unknown>;
          if (!active) return;
          if (!response.ok) throw new Error(errorMessage(value, "추출 상태를 다시 확인해 주세요."));
          setAssetMessage(String(value.stage_label || "에셋 추출 중"));
          if (value.status === "done") {
            const id = String(value.asset_id || "");
            if (!id) throw new Error("추출 결과의 에셋을 찾지 못했습니다.");
            await loadAssets();
            if (!active) return;
            setSelectedAssetIds(current => current.includes(id) ? current : current.length < 8 ? [...current, id] : current);
            setPendingExtraction("");
            setAssetMessage("AI 추출 결과를 저장했습니다. 라이브러리에서 확인할 수 있습니다.");
            return;
          }
          if (["error", "stale"].includes(String(value.status))) { setPendingExtraction(""); throw new Error(String(value.error || "에셋 추출이 중단되었습니다.")); }
          await new Promise(resolve => window.setTimeout(resolve, 1000));
        }
      } catch (error) { if (active) setAssetMessage(error instanceof Error ? error.message : "추출 상태를 확인하지 못했습니다."); }
      finally { if (active) setExtracting(false); }
    })();
    return () => { active = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draftLoaded, pendingExtraction, pollEpoch]);

  const chooseAssetSource = (file: File) => {
    if (assetSourceUrl) URL.revokeObjectURL(assetSourceUrl);
    setAssetSource(file);
    setAssetSourceUrl(URL.createObjectURL(file));
    setAssetName(file.name.replace(/\.[^.]+$/, ""));
    setAssetDescription("");
    setAssetBox(null);
    setAssetDragStart(null);
    setAssetMessage("추출할 에셋 전체를 박스로 선택한 뒤, 어떤 에셋인지 설명해 주세요.");
  };

  const chooseExtractionMode = (mode: ExtractionMode) => {
    setExtractionMode(mode);
    setAssetMessage(mode === "ai"
      ? "AI 추출이 기본입니다. 박스와 텍스트 설명을 함께 사용해 대상을 정확히 분리합니다."
      : "SAM은 생성 없이 원본 픽셀을 마스킹합니다. 에셋 전체를 박스로 감싸 주세요.");
  };

  const assetImageCoordinates = (event: ReactPointerEvent<HTMLDivElement>): [number, number] | null => {
    const image = sourceImageRef.current;
    if (!image) return null;
    const rect = image.getBoundingClientRect();
    if (!rect.width || !rect.height) return null;
    const x = Math.round(clamp((event.clientX - rect.left) / rect.width, 0, 1) * image.naturalWidth);
    const y = Math.round(clamp((event.clientY - rect.top) / rect.height, 0, 1) * image.naturalHeight);
    return [x, y];
  };

  const startAssetBox = (event: ReactPointerEvent<HTMLDivElement>) => {
    const point = assetImageCoordinates(event);
    if (!point) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    setAssetDragStart(point);
    setAssetBox([point[0], point[1], point[0], point[1]]);
    setAssetMessage("박스 크기를 조절하는 중입니다. 가구 전체가 안에 들어오게 잡아주세요.");
  };

  const moveAssetBox = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (!assetDragStart || !event.currentTarget.hasPointerCapture(event.pointerId)) return;
    const point = assetImageCoordinates(event);
    if (!point) return;
    setAssetBox([
      Math.min(assetDragStart[0], point[0]),
      Math.min(assetDragStart[1], point[1]),
      Math.max(assetDragStart[0], point[0]),
      Math.max(assetDragStart[1], point[1]),
    ]);
  };

  const finishAssetBox = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
    setAssetDragStart(null);
    setAssetBox((current) => {
      if (!current || current[2] - current[0] < 8 || current[3] - current[1] < 8) {
        setAssetMessage("점이 아니라 가구 전체를 감싸는 사각 박스를 드래그해 주세요.");
        return null;
      }
      setAssetMessage(extractionMode === "ai"
        ? "선택 박스를 확인했습니다. 에셋 설명을 입력하면 AI 추출을 실행할 수 있습니다."
        : "선택 박스를 확인했습니다. SAM 박스 추출을 실행하세요.");
      return current;
    });
  };

  const extractAsset = async () => {
    if (!assetSource || !assetBox || extracting || pendingExtraction || saveConflict || (extractionMode === "ai" && !assetDescription.trim())) return;
    setExtracting(true);
    setAssetMessage(extractionMode === "ai"
      ? "AI가 박스와 설명을 함께 읽고 선택한 에셋만 투명 PNG로 추출하고 있습니다."
      : "SAM이 선택한 에셋의 경계를 분석하고 있습니다.");
    try {
      const form = new FormData();
      form.append("file", assetSource);
      form.append("mode", extractionMode === "ai" ? "ai_box" : "sam_box");
      form.append("prompt", JSON.stringify({ box: assetBox }));
      form.append("description", assetDescription.trim());
      form.append("ai_mode", assetAiMode);
      form.append("save", "true");
      form.append("asset_name", assetName.trim() || "새 가구 에셋");
      form.append("category", "furniture");
      form.append("tags", "interior-composer");
      const response = await fetch("/api/extract", { method: "POST", body: form });
      const payload: unknown = await response.json().catch(() => ({}));
      if (!response.ok || !payload || typeof payload !== "object") {
        throw new Error(errorMessage(payload, "가구 에셋을 추출하지 못했습니다."));
      }
      const extraction = payload as Record<string, unknown>;
      const id = typeof extraction.asset_id === "string" ? extraction.asset_id : "";
      if (!id && typeof extraction.job_id === "string") {
        setPendingExtraction(extraction.job_id);
        return;
      }
      if (!id) throw new Error("AI 에셋 추출 결과를 저장하지 못했습니다.");
      await loadAssets();
      setSelectedAssetIds((current) => current.includes(id) ? current : current.length < 8 ? [...current, id] : current);
      setAssetMessage(`${extractionMode === "ai" ? "AI" : "SAM"} 추출 결과를 투명 PNG로 저장하고 이번 합성 에셋으로 선택했습니다.`);
    } catch (error) {
      setAssetMessage(error instanceof Error ? error.message : "가구 에셋을 추출하지 못했습니다.");
    } finally {
      setExtracting(false);
    }
  };

  const toggleAsset = (assetId: string) => {
    setSelectedAssetIds((current) => {
      if (current.includes(assetId)) {
        setPlacements((items) => items.filter((item) => item.asset_id !== assetId));
        return current.filter((id) => id !== assetId);
      }
      if (current.length >= 8) {
        setAssetMessage("한 장면에는 가구를 최대 8개까지 배치할 수 있습니다.");
        return current;
      }
      return [...current, assetId];
    });
  };

  const selectFinish = (item: FinishItem) => {
    setFinishSelections((current) => current[item.category] === item.id
      ? Object.fromEntries(Object.entries(current).filter(([category]) => category !== item.category))
      : { ...current, [item.category]: item.id });
    setFinishTargets((current) => ({ ...current, [item.category]: current[item.category] || DEFAULT_TARGETS[item.category] || "auto" }));
  };

  const chooseBefore = (file: File) => {
    if (beforeUrl) URL.revokeObjectURL(beforeUrl);
    setBeforeFile(file);
    setBeforeUrl(URL.createObjectURL(file));
    setPlacements([]);
    setPlacementAssetId("");
    setStatus("Before를 불러왔습니다. 필요하면 에셋을 이미지 위로 드래그해 위치를 고정하세요.");
  };

  const startDrag = (event: DragEvent, assetId: string) => {
    event.dataTransfer.setData("text/interior-asset", assetId);
    event.dataTransfer.effectAllowed = "move";
  };

  const placeAssetAt = (assetId: string, clientX: number, clientY: number) => {
    if (!selectedAssetIds.includes(assetId) || !beforeCanvasRef.current) return;
    const rect = beforeCanvasRef.current.getBoundingClientRect();
    const width = 0.24;
    const height = 0.3;
    const x = clamp((clientX - rect.left) / rect.width - width / 2, 0, 1 - width);
    const y = clamp((clientY - rect.top) / rect.height - height / 2, 0, 1 - height);
    setPlacements(current => [...current.filter(item => item.asset_id !== assetId), { asset_id: assetId, x, y, width, height }]);
    setPlacementAssetId("");
    setStatus("지정한 배치 박스가 자동 배치보다 우선 적용됩니다.");
  };

  const dropAsset = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    const assetId = event.dataTransfer.getData("text/interior-asset");
    placeAssetAt(assetId, event.clientX, event.clientY);
  };

  const resizePlacement = (assetId: string, width: number) => {
    setPlacements((current) => current.map((item) => {
      if (item.asset_id !== assetId) return item;
      const ratio = item.height / item.width;
      const height = Math.min(0.8, width * ratio);
      return { ...item, width, height, x: Math.min(item.x, 1 - width), y: Math.min(item.y, 1 - height) };
    }));
  };

  const generate = async () => {
    if (!beforeFile || (!selectedAssetIds.length && !selectedFinishes.length) || working || pendingComposition || saveConflict) return;
    setWorking(true);
    setRejectedResult(null);
    setStatus("선택 에셋과 마감재를 검증하고 있습니다.");
    const inputKey = currentInputKey;
    try {
      const form = new FormData();
      form.append("before", beforeFile);
      form.append("asset_ids", JSON.stringify(selectedAssetIds));
      form.append("finishes", JSON.stringify(finishSelections));
      form.append("finish_targets", JSON.stringify(Object.fromEntries(Object.keys(finishSelections).map((category) => [category, finishTargets[category] || DEFAULT_TARGETS[category] || "auto"]))));
      form.append("placements", JSON.stringify(placements));
      form.append("request", request);
      form.append("ai_mode", composeAiMode);
      const response = await fetch("/api/interior-compose", { method: "POST", body: form });
      const payload: unknown = await response.json().catch(() => ({}));
      if (!response.ok || !payload || typeof payload !== "object" || !("job_id" in payload)) {
        throw new Error(errorMessage(payload, "부분 합성을 시작하지 못했습니다."));
      }
      const jobId = String(payload.job_id);
      setPendingComposition({ jobId, inputKey });
      setMobileStep(3);
    } catch (error) {
      const message = error instanceof TypeError ? "서버에 연결하지 못했습니다. 연결을 확인한 뒤 다시 시도해 주세요." : error instanceof Error ? error.message : "부분 합성에 실패했습니다.";
      setStatus(`${message} 이전 결과와 입력은 보존했습니다.`);
    } finally {
      setWorking(false);
    }
  };

  const requestGenerate = () => {
    if (!beforeFile || (!selectedAssetIds.length && !selectedFinishes.length) || working || pendingComposition || !draftLoaded || saveConflict) return;
    confirmTriggerRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    setShowComposeConfirm(true);
  };

  const closeComposeConfirm = () => {
    if (confirmDialogRef.current?.open) confirmDialogRef.current.close();
    setShowComposeConfirm(false);
    confirmTriggerRef.current?.focus({ preventScroll: true });
  };

  const approveVisualReview = async () => {
    if (!savedResult || qualityStatus !== "unverified" || !frameAligned || !Object.values(reviewChecks).every(Boolean) || reviewWorking) return;
    setReviewWorking(true);
    try {
      const response = await fetch(`/api/interior-compose/${savedResult.jobId}/review`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ result_revision: savedResult.revision, result_sha256: savedResult.sha256, ...reviewChecks }),
      });
      const payload: unknown = await response.json().catch(() => ({}));
      if (!response.ok || !payload || typeof payload !== "object") throw new Error(errorMessage(payload, "검수 기록을 저장하지 못했습니다."));
      adoptCompletedJob(payload as Record<string, unknown>, savedResult.inputKey);
      setReportMessage("이 결과에 대한 직접 검수 기록을 저장했습니다.");
    } catch (error) { setReportMessage(error instanceof Error ? error.message : "검수 기록을 저장하지 못했습니다."); }
    finally { setReviewWorking(false); }
  };

  const createReport = async () => {
    if (!composeJobId || !resultUrl || reportWorking || !readyForReport) return;
    setReportWorking(true);
    setReportMessage("생성 당시 입력과 검수한 결과를 보고서 버전으로 저장하고 있습니다.");
    try {
      const response = await fetch("/api/interior-reports", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          job_id: composeJobId,
          title: reportTitle,
          space_type: reportSpaceType,
          previous_report_id: reportRecord?.report_id || "",
        }),
      });
      const payload: unknown = await response.json().catch(() => ({}));
      if (!response.ok || !payload || typeof payload !== "object" || !("report_id" in payload)) {
        throw new Error(errorMessage(payload, "상담 보고서를 생성하지 못했습니다."));
      }
      const report = payload as ComposerReport;
      setReportRecord(report);
      setVerifiedBackup(null);
      setReportMessage(`상담 보고서 버전 ${report.version}을 저장했습니다. 웹 공유와 PDF 다운로드가 가능합니다.`);
    } catch (error) {
      setReportMessage(error instanceof Error ? error.message : "상담 보고서를 생성하지 못했습니다.");
    } finally {
      setReportWorking(false);
    }
  };

  const copyReportLink = async () => {
    if (!reportRecord) return;
    try {
      await navigator.clipboard.writeText(new URL(reportRecord.share_url, window.location.origin).toString());
      setReportMessage("공유 링크를 클립보드에 복사했습니다.");
    } catch {
      setReportMessage("공유 링크를 복사하지 못했습니다. 웹 보고서를 열어 주소를 복사해 주세요.");
    }
  };

  if (!draftLoaded) return <main className={styles.page}><h1>저장된 작업을 불러오고 있습니다.</h1><p role="status">사진과 선택 내용을 복원하고 있어요.</p></main>;

  const moveToStep = (step: number) => {
    setMobileStep(step);
    window.requestAnimationFrame(() => document.querySelector('[aria-label="합성 진행 단계"]')?.scrollIntoView({ block: "start" }));
  };

  return <main className={styles.page}>
    <header className={styles.hero}>
      <div>
        <p className={styles.eyebrow}>CONSTRAINED INTERIOR COMPOSER</p>
        <h1>선택한 디자인을 만들고,<br />원래 구도와 비교합니다.</h1>
      </div>
      <p>가구 에셋과 마감재를 준비한 뒤 Before 공간에 적용합니다. 생성 후 수량·배치·구도를 검사하고, 검수한 결과와 생성 당시 선택 내용을 함께 저장합니다.</p>
    </header>
    <p className={styles.saveStatus} role="status" aria-live="polite">{saveStatus}</p>
    {saveConflict && <div className={styles.saveStatus} role="alert"><p>이 탭의 내용과 생성 결과는 화면에 남아 있습니다. 최신 저장본을 불러오면 이 탭의 저장되지 않은 편집은 바뀝니다.</p><button type="button" className={styles.secondaryButton} onClick={() => window.location.reload()}>최신 저장본 불러오기</button></div>}

    <div className={styles.stepRail} aria-label="합성 진행 단계">
      {["에셋·마감재 준비", "Before·적용 위치", "After·품질검사"].map((label, index) => <a key={label} href={`#composer-step-${index + 1}`} className={styles.railItem} aria-current={mobileStep === index + 1 ? "step" : undefined} onClick={(event) => {
        if (window.matchMedia("(max-width:760px)").matches) { event.preventDefault(); moveToStep(index + 1); }
        else setMobileStep(index + 1);
      }}><b>{index + 1}</b><span>{label}</span></a>)}
    </div>

    <div className={styles.workspace}>
      <section className={styles.step} aria-labelledby="composer-step-1" data-mobile-active={mobileStep === 1}>
        <StepHeader number="01" title="에셋 준비" description="가구를 추출하고 적용할 마감재를 고릅니다." />

        <div className={styles.block}>
          <div className={styles.blockTitle}><Scissors aria-hidden="true" /><div><b>가구·소품 누끼 추출</b><small>AI가 기본이며 필요할 때 SAM을 선택할 수 있습니다.</small></div></div>
          <div className={styles.extractModePicker} role="group" aria-label="에셋 추출 방식">
            <button type="button" aria-pressed={extractionMode === "ai"} onClick={() => chooseExtractionMode("ai")}>
              <Sparkles aria-hidden="true" /><span><b>AI 추출</b><small>기본 · 설명까지 이해해 분리</small></span>
            </button>
            <button type="button" aria-pressed={extractionMode === "sam"} onClick={() => chooseExtractionMode("sam")}>
              <Scissors aria-hidden="true" /><span><b>SAM 추출</b><small>원본 픽셀 중심의 박스 마스크</small></span>
            </button>
          </div>
          {extractionMode === "ai" && <div className={styles.assetModeBlock}>
            <div className={styles.modeLabel}><b>AI 추출 모델</b><small>에셋 추출에만 적용됩니다.</small></div>
            <div className={styles.modePicker} role="group" aria-label="에셋 AI 추출 모델">
              <button type="button" aria-pressed={assetAiMode === "flash"} onClick={() => setAssetAiMode("flash")}>
                <b>Flash <span>(추천)</span></b><small>빠른 추출 · 예상 15~40초</small>
              </button>
              <button type="button" aria-pressed={assetAiMode === "premium"} onClick={() => setAssetAiMode("premium")}>
                <b>Premium</b><small>정밀 경계·디테일 · 예상 40~90초</small>
              </button>
            </div>
          </div>}
          <label className={styles.uploadBox}>
            <Upload aria-hidden="true" />
            <span>{assetSource ? "다른 이미지 선택" : "혼합 이미지 업로드"}</span>
            <input type="file" accept="image/*" onChange={(event) => { const file = event.target.files?.[0]; if (file) chooseAssetSource(file); }} />
          </label>
          {assetSourceUrl && <div className={styles.sourcePicker} onPointerDown={startAssetBox} onPointerMove={moveAssetBox} onPointerUp={finishAssetBox} onPointerCancel={finishAssetBox}>
            <img ref={sourceImageRef} src={assetSourceUrl} alt="에셋 추출 원본" draggable={false} />
            {assetBox && sourceImageRef.current && <span className={styles.selectionBox} style={{
              left: `${(assetBox[0] / sourceImageRef.current.naturalWidth) * 100}%`,
              top: `${(assetBox[1] / sourceImageRef.current.naturalHeight) * 100}%`,
              width: `${((assetBox[2] - assetBox[0]) / sourceImageRef.current.naturalWidth) * 100}%`,
              height: `${((assetBox[3] - assetBox[1]) / sourceImageRef.current.naturalHeight) * 100}%`,
            }} />}
          </div>}
          {assetSource && <div className={styles.extractControls}>
            <div className={styles.extractFields}>
              <label>에셋 이름<input value={assetName} onChange={(event) => setAssetName(event.target.value)} placeholder="예: 라운지 체어 01" /></label>
              <label>어떤 에셋인가요? {extractionMode === "ai" && <em>AI 필수 입력</em>}
                <textarea value={assetDescription} onChange={(event) => setAssetDescription(event.target.value)} rows={2} placeholder="예: 월넛 프레임과 베이지 패브릭의 1인 암체어" />
              </label>
            </div>
            <button type="button" className={styles.primaryButton} onClick={() => void extractAsset()} disabled={extracting || Boolean(pendingExtraction) || saveConflict || !assetBox || (extractionMode === "ai" && !assetDescription.trim())}>
              {extracting ? <LoaderCircle className={styles.spin} aria-hidden="true" /> : <Wand2 aria-hidden="true" />}
              {extracting ? "추출 중" : extractionMode === "ai" ? "AI로 에셋 추출" : "SAM 박스 추출"}
            </button>
          </div>}
          <p className={styles.message} role="status">{assetMessage}</p>
          {extractionMode === "ai" && <p className={styles.hint}>AI 추출에는 선택한 공급자의 이미지 사용량이 발생합니다.</p>}
          {pendingExtraction && !extracting && <button type="button" className={styles.secondaryButton} onClick={() => setPollEpoch(value => value + 1)}>추출 상태 다시 확인</button>}
        </div>

        <div className={styles.block}>
          <div className={styles.blockTitle}><Layers3 aria-hidden="true" /><div><b>사용할 에셋</b><small>선택한 항목은 각각 한 번만 배치</small></div><em>{selectedAssetIds.length}/8</em></div>
          <div className={styles.assetGrid}>
            {assets.length ? assets.slice(0, assetLimit).map((asset) => {
              const selected = selectedAssetIds.includes(asset.asset_id);
              return <button key={asset.asset_id} type="button" data-asset-id={asset.asset_id} aria-pressed={selected} className={styles.assetCard} onClick={() => toggleAsset(asset.asset_id)} draggable={selected} onDragStart={(event) => startDrag(event, asset.asset_id)}>
                <span className={styles.assetImage}><img src={asset.url} alt="" />{selected && <i><Check aria-hidden="true" /></i>}</span>
                <b>{asset.asset_name}</b>
              </button>;
            }) : <p className={styles.empty}>저장된 에셋이 없습니다.</p>}
          </div>
          <div className={styles.libraryFooter}><small>{Math.min(assetLimit, assets.length)} / {assets.length}개 표시</small>{assets.length > assetLimit && <button type="button" onClick={() => setAssetLimit(value => value + 8)}>에셋 더 보기</button>}</div>
        </div>

        <div className={styles.block}>
          <div className={styles.blockTitle}><Sparkles aria-hidden="true" /><div><b>마감재 라이브러리</b><small>카테고리별 한 가지 선택</small></div></div>
          <div className={styles.categoryTabs}>
            {catalog.categories.map((category) => <button key={category.key} type="button" aria-pressed={activeFinishCategory === category.key} onClick={() => { setActiveFinishCategory(category.key); setFinishLimit(8); }}>{CATEGORY_LABELS[category.key] || category.label}</button>)}
          </div>
          <div className={styles.swatchGrid}>
            {visibleFinishItems.slice(0, finishLimit).map((item) => {
              const selected = finishSelections[item.category] === item.id;
              return <button key={item.id} type="button" data-finish-id={item.id} aria-pressed={selected} className={styles.swatch} onClick={() => selectFinish(item)}>
                <span>{item.image_url ? <img src={item.image_url} alt="" /> : <i />}{selected && <Check aria-hidden="true" />}</span>
                <b>{item.name}</b><small>{[item.tone, item.finish].filter(Boolean).join(" · ")}</small>
              </button>;
            })}
          </div>
          <div className={styles.libraryFooter}><small>{Math.min(finishLimit, visibleFinishItems.length)} / {visibleFinishItems.length}개 표시</small>{visibleFinishItems.length > finishLimit && <button type="button" onClick={() => setFinishLimit(value => value + 8)}>마감재 더 보기</button>}</div>
        </div>
      </section>

      <section className={styles.step} aria-labelledby="composer-step-2" data-mobile-active={mobileStep === 2}>
        <StepHeader number="02" title="공간 설정" description="Before와 적용 위치를 지정합니다." />

        <div className={styles.block}>
          <div className={styles.blockTitle}><ImageIcon aria-hidden="true" /><div><b>Before 업로드</b><small>실사 사진 또는 3D 렌더링</small></div></div>
          {!beforeUrl ? <label className={`${styles.uploadBox} ${styles.beforeUpload}`}>
            <Upload aria-hidden="true" /><span>Before 이미지 선택</span><small>카메라 구도와 화면 비율을 고정합니다.</small>
            <input type="file" accept="image/*" onChange={(event) => { const file = event.target.files?.[0]; if (file) chooseBefore(file); }} />
          </label> : <>
            <div ref={beforeCanvasRef} className={styles.beforeCanvas} data-testid="before-canvas" data-placement-active={Boolean(placementAssetId)} onDragOver={(event) => event.preventDefault()} onDrop={dropAsset} onClick={(event) => { if (placementAssetId) placeAssetAt(placementAssetId, event.clientX, event.clientY); }}>
              <img src={beforeUrl} alt="Before 공간" />
              {placements.map((placement) => {
                const asset = assets.find((item) => item.asset_id === placement.asset_id);
                return <div key={placement.asset_id} className={styles.placementBox} style={{ left: `${placement.x * 100}%`, top: `${placement.y * 100}%`, width: `${placement.width * 100}%`, height: `${placement.height * 100}%` }}>
                  {asset && <img src={asset.url} alt="" />}
                  <span>{asset?.asset_name}</span>
                  <button type="button" aria-label={`${asset?.asset_name || "가구"} 위치 지정 해제`} onClick={(event) => { event.stopPropagation(); setPlacements((current) => current.filter((item) => item.asset_id !== placement.asset_id)); }}><X aria-hidden="true" /></button>
                </div>;
              })}
            </div>
            <label className={styles.changeFile}>Before 변경<input type="file" accept="image/*" onChange={(event) => { const file = event.target.files?.[0]; if (file) chooseBefore(file); }} /></label>
          </>}
          <p className={styles.hint}><Move aria-hidden="true" /> STEP 1의 선택 에셋을 이미지로 끌어놓으면 그 위치가 우선됩니다. 놓지 않은 에셋은 AI가 최적으로 배치합니다.</p>
          {beforeUrl && selectedAssets.length > 0 && <div className={styles.tapPlacement}>
            <label>클릭·터치로 위치 지정<select value={placementAssetId} onChange={event => setPlacementAssetId(event.target.value)}><option value="">가구 선택</option>{selectedAssets.map(asset => <option key={asset.asset_id} value={asset.asset_id}>{asset.asset_name}</option>)}</select></label>
            {placementAssetId && <p>사진에서 배치할 곳을 눌러주세요. <button type="button" onClick={() => { const rect = beforeCanvasRef.current?.getBoundingClientRect(); if (rect) placeAssetAt(placementAssetId, rect.left + rect.width / 2, rect.top + rect.height * .7); }}>중앙에 배치</button><button type="button" onClick={() => setPlacementAssetId("")}>취소</button></p>}
          </div>}
        </div>

        {placements.length > 0 && <div className={styles.block}>
          <div className={styles.blockTitle}><Move aria-hidden="true" /><div><b>지정 배치 크기</b><small>박스 크기는 가구의 목표 크기입니다.</small></div></div>
          <div className={styles.placementControls}>{placements.map((placement) => {
            const asset = assets.find((item) => item.asset_id === placement.asset_id);
            return <label key={placement.asset_id}><span>{asset?.asset_name}</span><input type="range" min="0.12" max="0.5" step="0.01" value={placement.width} onChange={(event) => resizePlacement(placement.asset_id, Number(event.target.value))} /></label>;
          })}</div>
        </div>}

        <div className={styles.block}>
          <div className={styles.blockTitle}><Layers3 aria-hidden="true" /><div><b>마감 적용 면</b><small>AI 인식 후 이 지정값을 우선 적용</small></div></div>
          {selectedFinishes.length ? <div className={styles.targetList}>{selectedFinishes.map((item) => <label key={item.id}>
            <span>{item.image_url && <img src={item.image_url} alt="" />}<b>{CATEGORY_LABELS[item.category] || item.category}</b><small>{item.name}</small></span>
            <select aria-label={`${CATEGORY_LABELS[item.category] || item.category} 적용 면`} value={finishTargets[item.category] || DEFAULT_TARGETS[item.category] || "auto"} onChange={(event) => setFinishTargets((current) => ({ ...current, [item.category]: event.target.value }))}>
              {TARGET_OPTIONS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select>
          </label>)}</div> : <p className={styles.empty}>STEP 1에서 마감재를 선택하면 적용 면을 지정할 수 있습니다.</p>}
        </div>

        <div className={styles.block}>
          <label className={styles.noteLabel}>추가 요청 <small>배치 철칙을 바꾸지 않는 범위의 선호만 입력</small><textarea rows={3} maxLength={3000} value={request} onChange={(event) => setRequest(event.target.value)} placeholder="예: 창가의 자연광을 살리고 여백이 넓게 보이도록 배치" /></label>
        </div>
      </section>

      <section className={styles.step} aria-labelledby="composer-step-3" data-mobile-active={mobileStep === 3}>
        <StepHeader number="03" title="After 생성" description="부분 합성하고 자동 기준을 점검합니다." />

        <div className={styles.block}>
          <div className={styles.blockTitle}><Wand2 aria-hidden="true" /><div><b>이미지 모델</b><small>동일 입력과 동일한 제약 조건 사용</small></div></div>
          <div className={styles.modePicker} role="group" aria-label="After 이미지 생성 모델">
            <button type="button" aria-pressed={composeAiMode === "flash"} onClick={() => setComposeAiMode("flash")}><b>Flash</b><small>빠른 배치 확인 · 예상 30~60초</small></button>
            <button type="button" aria-pressed={composeAiMode === "premium"} onClick={() => setComposeAiMode("premium")}><b>Premium <span>(추천)</span></b><small>정밀 시점·재질 합성 · 예상 1~2분</small></button>
          </div>
          <div className={styles.summary}>
            <span><b>{selectedAssetIds.length}</b> 가구</span>
            <span><b>{selectedFinishes.length}</b> 마감재</span>
            <span><b>{placements.length}</b> 지정 위치</span>
          </div>
          {!selectedAssetIds.length && selectedFinishes.length > 0 && <p className={styles.finishOnlyNotice}><ShieldCheck aria-hidden="true" />가구·소품은 유지하고 선택한 마감재만 적용합니다.</p>}
          <button type="button" className={styles.generateButton} disabled={!beforeFile || (!selectedAssetIds.length && !selectedFinishes.length) || working || Boolean(pendingComposition) || saveConflict} onClick={requestGenerate}>
            {working ? <LoaderCircle className={styles.spin} aria-hidden="true" /> : <Sparkles aria-hidden="true" />}
            {working ? "부분 합성 진행 중" : "After 부분 합성 시작"}
          </button>
          <p className={styles.status} role="status" aria-live="polite">{status}</p>
          {pendingComposition && !working && <button type="button" className={styles.secondaryButton} onClick={() => setPollEpoch(value => value + 1)}>합성 상태 다시 확인</button>}
          {resultIsStale && <p className={styles.reviewNotice}>현재 편집은 아래 결과에 아직 반영되지 않았습니다. 보고서는 결과를 생성했을 때의 선택 정보로 저장됩니다.</p>}
        </div>

        <div className={`${styles.block} ${styles.resultBlock}`}>
          <div className={styles.blockTitle}><ImageIcon aria-hidden="true" /><div><b>Before / After</b><small>같은 구도에서 슬라이더 비교</small></div></div>
          {working && savedResult && <p className={styles.hint}>새 결과를 만드는 동안 이전 결과를 표시합니다.</p>}
          {(savedResult?.sourceUrl || beforeUrl) && resultUrl ? <BeforeAfterSlider before={savedResult?.sourceUrl || beforeUrl} after={resultUrl} /> : <div className={styles.resultPlaceholder}>
            {working ? <><LoaderCircle className={styles.spin} aria-hidden="true" /><b>선택 영역을 합성하고 있습니다.</b><small>전체 공간을 새로 만들지 않고 변경 영역만 편집합니다.</small></> : <><ImageIcon aria-hidden="true" /><b>After가 여기에 표시됩니다.</b><small>Before와 가구 에셋 또는 마감재를 준비해 주세요.</small></>}
          </div>}
          {rejectedResult && <div className={styles.reviewNotice}><b>새 결과는 검수를 통과하지 못했습니다.</b>{rejectedResult.messages.map((message, index) => <p key={index}>{message}</p>)}<a href={rejectedResult.url} target="_blank" rel="noreferrer">검수 실패 이미지 확인</a><p>현재 표시된 검수 완료본과 보고서는 그대로 보존했습니다.</p></div>}
          {verifiedBackup && !readyForReport && <div className={styles.reviewNotice}><p>이전 검수 완료본도 보존하고 있습니다.</p><button type="button" className={styles.secondaryButton} onClick={() => { setSavedResult(verifiedBackup.result); setReportRecord(verifiedBackup.report); setVerifiedBackup(null); setRejectedResult(null); setStatus("이전 검수 완료본과 보고서를 복원했습니다."); }}>이전 검수 완료본으로 돌아가기</button>{verifiedBackup.report && <p><a href={verifiedBackup.report.share_url} target="_blank" rel="noreferrer">이전 검수 보고서 열기</a></p>}</div>}
        </div>

        <div className={styles.block}>
          <div className={styles.blockTitle}><ShieldCheck aria-hidden="true" /><div><b>결과 검수</b><small>구도 정렬과 실제 이미지 검수를 구분해 확인합니다.</small></div></div>
          <ul className={styles.qaList}>
            <li className={frameAligned ? styles.passed : undefined}>{frameAligned ? <Check aria-hidden="true" /> : <ShieldCheck aria-hidden="true" />}<span>Before 좌표 정렬: {frameAligned ? `완료${savedResult?.alignmentMethod === "affine_registration_full_frame" ? " · 전체 프레임 정렬" : unchangedPixelRatio !== null ? ` · 원본 픽셀 ${(unchangedPixelRatio * 100).toFixed(1)}% 유지` : ""}` : savedResult ? "실패 · 재생성 필요" : "대기"}</span></li>
            <li className={qualityStatus === "passed" ? styles.passed : undefined}>{qualityStatus === "passed" ? <Check aria-hidden="true" /> : <ShieldCheck aria-hidden="true" />}<span>가구 수량·배치·마감 검사: {!savedResult ? "대기" : qualityStatus === "passed" ? "AI 시각 검수 통과" : qualityStatus === "failed" ? "불일치 발견 · 보정 필요" : readyForReport ? "사용자 직접 확인 완료" : "직접 확인 필요"}</span></li>
          </ul>
          {savedResult?.review?.issues?.map((issue, index) => <p key={index} className={styles.reviewNotice}>{issue.message || "검수에서 확인이 필요한 항목이 있습니다."}</p>)}
          {savedResult && !frameAligned && <p className={styles.reviewNotice}>좌표 정렬에 실패한 결과는 보고서로 저장할 수 없습니다. 기존 결과는 유지한 채 다시 생성해 주세요.</p>}
          {savedResult && frameAligned && qualityStatus === "unverified" && !readyForReport && <div className={styles.visualReview}>
            <p>자동 판독이 확실하지 않습니다. 원본과 결과를 직접 비교한 뒤 확인해 주세요.</p>
            {([ ["inventory", "선택한 가구의 종류·수량과 배치가 맞습니다."], ["structure", "창·문·벽과 원래 구도를 확인했습니다."], ["finishes", "마감재가 지정한 면에 적용됐습니다."] ] as const).map(([key, label]) => <label key={key}><input type="checkbox" checked={reviewChecks[key]} onChange={event => setReviewChecks(current => ({ ...current, [key]: event.target.checked }))} />{label}</label>)}
            <button type="button" className={styles.secondaryButton} disabled={reviewWorking || !Object.values(reviewChecks).every(Boolean)} onClick={() => void approveVisualReview()}>{reviewWorking ? "검수 저장 중" : "이 결과의 직접 검수 저장"}</button>
          </div>}
          {savedResult && <details className={styles.usageDetails}><summary>이번 결과의 사용량 기록 · 이미지 {savedResult.providerCalls?.length || 0}회 / 검수 {savedResult.qualityCalls?.filter(call => call.provider_invoked).length || 0}회</summary>
            {[...(savedResult.providerCalls || []), ...(savedResult.qualityCalls || []).filter(call => call.provider_invoked)].map((call, index) => <div key={index}><b>{call.model || "모델 정보 없음"}</b><span>{Object.keys(call.usage || {}).length ? Object.entries(call.usage || {}).filter(([, count]) => typeof count === "number").map(([key, count]) => `${key}: ${count}`).join(" · ") || "상세 사용량 기록 있음" : "공급자가 사용량을 반환하지 않았습니다."}</span></div>)}
            <p>호출 기록과 공급자 사용량입니다. 확정 청구 금액은 공급자의 사용 내역에서 확인할 수 있습니다.</p>
          </details>}
        </div>
      </section>
    </div>
    <nav className={styles.mobileActions} aria-label="모바일 단계 이동"><button type="button" disabled={mobileStep === 1} onClick={() => moveToStep(mobileStep - 1)}>이전 단계</button><span>{mobileStep} / 3</span><button type="button" disabled={mobileStep === 3} onClick={() => moveToStep(mobileStep + 1)}>다음 단계</button></nav>
    {resultUrl && composeJobId && <section className={styles.reportPanel} aria-labelledby="composer-report-title" data-mobile-active={mobileStep === 3}>
      <div className={styles.reportIntro}>
        <p>CONSULTATION REPORT</p>
        <h2 id="composer-report-title">이 결과를 상담 보고서로 정리합니다.</h2>
        <small>검수한 Before/After와 생성 당시의 에셋·마감재·배치 정보를 함께 저장합니다. 이후 편집한 선택으로 기존 이미지의 설명을 바꾸지 않습니다.</small>
      </div>
      <div className={styles.reportBuilder}>
        <label>보고서 제목<input value={reportTitle} maxLength={120} onChange={(event) => setReportTitle(event.target.value)} /></label>
        <label>공간 유형<input value={reportSpaceType} maxLength={80} onChange={(event) => setReportSpaceType(event.target.value)} placeholder="예: 거실, 침실, 카페 홀" /></label>
        <div className={styles.reportSnapshot}>
          <span><b>{savedResult?.snapshot?.assets?.length ?? 0}</b>결과의 에셋</span>
          <span><b>{savedResult?.snapshot?.finishes?.length ?? 0}</b>결과의 마감재</span>
          <span><b>{savedResult?.snapshot?.placements?.length ?? 0}</b>결과의 지정 배치</span>
        </div>
        <button type="button" className={styles.reportCreateButton} onClick={() => void createReport()} disabled={reportWorking || !reportTitle.trim() || !readyForReport}>
          {reportWorking ? <LoaderCircle className={styles.spin} aria-hidden="true" /> : <FileText aria-hidden="true" />}
          {reportWorking ? "보고서 저장 중" : reportRecord ? "새 보고서 버전 저장" : "상담 보고서 생성"}
        </button>
        {!readyForReport && <p className={styles.reportMessage}>결과 검수를 완료하면 보고서를 저장할 수 있습니다.</p>}
        <p className={styles.reportMessage} role="status" aria-live="polite">{reportMessage}</p>
        {reportRecord && <div className={styles.reportReady}>
          <div><b>VERSION {String(reportRecord.version).padStart(2, "0")}</b><small>총 {reportRecord.versions.length}개 버전</small></div>
          <a href={reportRecord.share_url} target="_blank" rel="noreferrer"><ExternalLink aria-hidden="true" />웹 보고서 열기</a>
          <a href={reportRecord.pdf_url}><Download aria-hidden="true" />PDF 다운로드</a>
          <button type="button" onClick={() => void copyReportLink()}><Copy aria-hidden="true" />공유 링크 복사</button>
        </div>}
      </div>
    </section>}
    {showComposeConfirm && <dialog ref={confirmDialogRef} className={styles.confirmBackdrop} aria-labelledby="compose-confirm-title" onCancel={event => { event.preventDefault(); closeComposeConfirm(); }} onClose={() => { setShowComposeConfirm(false); confirmTriggerRef.current?.focus({ preventScroll: true }); }} onClick={event => { if (event.target === event.currentTarget) closeComposeConfirm(); }} onKeyDown={event => {
      if (event.key !== "Tab") return;
      const controls = Array.from(event.currentTarget.querySelectorAll<HTMLElement>('button:not(:disabled),input:not(:disabled),select:not(:disabled),textarea:not(:disabled),a[href],[tabindex="0"]')).filter(element => element.getClientRects().length > 0);
      const first = controls[0]; const last = controls[controls.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    }}>
      <div className={styles.confirmDialog} onClick={event => event.stopPropagation()}>
        <span className={styles.confirmIcon}><Sparkles aria-hidden="true" /></span>
        <p>적용 내용 확인</p>
        <h2 id="compose-confirm-title">{!selectedAssetIds.length
          ? <>에셋을 선택하지 않았는데<br />이대로 진행할까요?</>
          : !selectedFinishes.length
            ? <>마감재를 선택하지 않았는데<br />이대로 진행할까요?</>
            : <>선택한 내용으로<br />After를 생성할까요?</>}
        </h2>
        <div className={styles.confirmChecklist}>
          <div data-checked={selectedAssetIds.length > 0}>
            <i>{selectedAssetIds.length ? <Check aria-hidden="true" /> : <X aria-hidden="true" />}</i>
            <span><b>에셋</b><small>{selectedAssetIds.length ? `${selectedAssetIds.length}개 선택됨` : "선택 안 함"}</small></span>
          </div>
          <div data-checked={selectedFinishes.length > 0}>
            <i>{selectedFinishes.length ? <Check aria-hidden="true" /> : <X aria-hidden="true" />}</i>
            <span><b>마감재</b><small>{selectedFinishes.length ? `${selectedFinishes.length}개 선택됨` : "선택 안 함"}</small></span>
          </div>
        </div>
        <small>{!selectedAssetIds.length
          ? `기존 가구·소품은 유지하고 선택한 마감재 ${selectedFinishes.length}개만 적용합니다.`
          : !selectedFinishes.length
            ? `기존 이동식 가구·소품을 제거하고 선택한 에셋 ${selectedAssetIds.length}개만 배치하며, 기존 마감은 유지합니다.`
            : `기존 이동식 가구·소품을 제거하고 선택한 에셋 ${selectedAssetIds.length}개와 마감재 ${selectedFinishes.length}개를 적용합니다.`}</small>
        <small>자동 시각 검수와 필요한 경우 보정 1회가 포함됩니다. 추가 AI 사용량이 발생할 수 있습니다.</small>
        <div className={styles.confirmActions}>
          <button type="button" onClick={closeComposeConfirm}>취소</button>
          <button type="button" onClick={() => { closeComposeConfirm(); void generate(); }}>이 설정으로 적용</button>
        </div>
      </div>
    </dialog>}
  </main>;
}

function StepHeader({ number, title, description }: { number: string; title: string; description: string }) {
  return <header className={styles.stepHeader}>
    <span>{number}</span>
    <div><p>STEP {Number(number)}</p><h2 id={`composer-step-${Number(number)}`}>{title}</h2><small>{description}</small></div>
  </header>;
}
