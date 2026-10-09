"use client";

import Link from "next/link";
import { ArrowLeft, Copy, Download, FileText, Layers3 } from "lucide-react";
import { useEffect, useState } from "react";

import BeforeAfterSlider from "@/components/journey/BeforeAfterSlider";
import styles from "./InteriorConsultationReport.module.css";

type ReportAsset = {
  asset_id: string;
  name: string;
  category: string;
  placement: "manual" | "automatic";
  image_url?: string | null;
};

type ReportFinish = {
  id: string;
  name: string;
  category: string;
  category_label: string;
  target: string;
  target_label: string;
  image_url?: string | null;
};

type ReportVersion = { report_id: string; version: number; created_at: string; title: string };

type ConsultationReport = {
  report_id: string;
  version: number;
  title: string;
  space_type: string;
  created_at: string;
  ai_mode: string;
  before_url: string;
  after_url: string;
  assets: ReportAsset[];
  finishes: ReportFinish[];
  request: string;
  consultation: { summary: string; notes: string[] };
  disclaimer: string;
  versions: ReportVersion[];
  pdf_url: string;
  share_url: string;
  verification?: { frame_aligned?: boolean; quality_status?: string; quality_method?: string; manual_visual_review?: boolean };
};

function reportDate(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : new Intl.DateTimeFormat("ko-KR", { year: "numeric", month: "long", day: "numeric", timeZone: "Asia/Seoul" }).format(date);
}

export default function InteriorConsultationReport({ reportId }: { reportId: string }) {
  const [report, setReport] = useState<ConsultationReport | null>(null);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  useEffect(() => {
    let active = true;
    fetch(`/api/interior-reports/${encodeURIComponent(reportId)}`, { cache: "no-store" })
      .then(async (response) => {
        const payload: unknown = await response.json().catch(() => ({}));
        if (!response.ok || !payload || typeof payload !== "object") throw new Error("상담 보고서를 불러오지 못했습니다.");
        return payload as ConsultationReport;
      })
      .then((value) => { if (active) setReport(value); })
      .catch((reason) => { if (active) setError(reason instanceof Error ? reason.message : "상담 보고서를 불러오지 못했습니다."); });
    return () => { active = false; };
  }, [reportId]);

  const copyLink = async () => {
    if (!report) return;
    try {
      await navigator.clipboard.writeText(new URL(report.share_url, window.location.origin).toString());
      setMessage("공유 링크를 복사했습니다.");
    } catch {
      setMessage("링크를 복사하지 못했습니다. 브라우저 주소를 복사해 주세요.");
    }
  };

  if (error) return <main className={styles.state}><FileText aria-hidden="true" /><h1>{error}</h1><Link href="/tools/interior-composer">Composer로 돌아가기</Link></main>;
  if (!report) return <main className={styles.state}><FileText aria-hidden="true" /><h1>상담 보고서를 준비하고 있습니다.</h1></main>;

  return <main className={styles.page}>
    <header className={styles.toolbar}>
      <Link href="/tools/interior-composer"><ArrowLeft aria-hidden="true" />Composer</Link>
      <div>
        <button type="button" onClick={() => void copyLink()}><Copy aria-hidden="true" />공유 링크</button>
        <a href={report.pdf_url}><Download aria-hidden="true" />PDF 다운로드</a>
      </div>
    </header>

    <article className={styles.report}>
      <section className={styles.cover}>
        <div>
          <p>BEFORE / AFTER CONSULTATION</p>
          <h1>{report.title}</h1>
          <dl>
            <div><dt>공간</dt><dd>{report.space_type}</dd></div>
            <div><dt>버전</dt><dd>VERSION {String(report.version).padStart(2, "0")}</dd></div>
            <div><dt>발행일</dt><dd>{reportDate(report.created_at)}</dd></div>
            <div><dt>생성 모드</dt><dd>{report.ai_mode === "premium" ? "Premium" : "Flash"}</dd></div>
            <div><dt>검수</dt><dd>{report.verification?.manual_visual_review ? "사용자 직접 확인" : report.verification?.quality_status === "passed" ? "AI 시각 검수 통과" : "이전 저장 기록 · 확인 필요"}</dd></div>
          </dl>
        </div>
        <img src={report.after_url} alt="제안된 After 공간" />
      </section>

      <section className={styles.section}>
        <div className={styles.sectionHeading}><p>01 · BEFORE / AFTER</p><h2>원본과 나란히 확인하는 변화.</h2><small>{report.verification ? "생성 당시 원본과 검수한 결과를 비교합니다." : "이전 버전의 저장 결과입니다. 사용 전 원본과 결과를 직접 확인해 주세요."}</small></div>
        <div className={styles.slider}><BeforeAfterSlider before={report.before_url} after={report.after_url} /></div>
        <div className={styles.staticPair} aria-hidden="true"><img src={report.before_url} alt="" /><img src={report.after_url} alt="" /></div>
      </section>

      <section className={styles.section}>
        <div className={styles.sectionHeading}><p>02 · APPLIED INVENTORY</p><h2>생성 당시 선택한 항목.</h2><small>입력 버전에 저장한 에셋과 마감재입니다. 이미지의 실제 적용 상태는 원본 비교와 검수 기록을 함께 확인해 주세요.</small></div>
        <div className={styles.inventorySummary}><span><b>{report.assets.length}</b>에셋</span><span><b>{report.finishes.length}</b>마감재</span></div>
        <h3><Layers3 aria-hidden="true" />가구·소품 에셋</h3>
        <div className={styles.itemGrid}>
          {report.assets.length ? report.assets.map((asset) => <article key={asset.asset_id} className={styles.itemCard}>
            <div>{asset.image_url ? <img src={asset.image_url} alt={asset.name} /> : <FileText aria-hidden="true" />}</div>
            <p>{asset.category}</p><b>{asset.name}</b><small>{asset.placement === "manual" ? "사용자 지정 위치" : "AI 배치"} · 참조 단위 1회</small>
          </article>) : <p className={styles.empty}>선택한 에셋이 없어 기존 가구와 소품을 유지했습니다.</p>}
        </div>
        <h3><Layers3 aria-hidden="true" />적용 마감재</h3>
        <div className={styles.itemGrid}>
          {report.finishes.length ? report.finishes.map((finish) => <article key={finish.id} className={styles.itemCard}>
            <div>{finish.image_url ? <img src={finish.image_url} alt={finish.name} /> : <FileText aria-hidden="true" />}</div>
            <p>{finish.category_label}</p><b>{finish.name}</b><small>{finish.target_label} 적용</small>
          </article>) : <p className={styles.empty}>선택한 마감재가 없어 기존 마감 상태를 유지했습니다.</p>}
        </div>
      </section>

      <section className={styles.section}>
        <div className={styles.sectionHeading}><p>03 · DESIGN CONSULTATION</p><h2>배치와 적용의 근거.</h2><small>{report.consultation.summary}</small></div>
        {report.request && <blockquote><b>추가 요청</b>{report.request}</blockquote>}
        <ol className={styles.notes}>{report.consultation.notes.map((note, index) => <li key={`${index}-${note}`}><span>{String(index + 1).padStart(2, "0")}</span><p>{note}</p></li>)}</ol>
      </section>

      <section className={styles.versions}>
        <div><p>REPORT HISTORY</p><h2>저장된 보고서 버전</h2></div>
        <nav aria-label="보고서 버전">
          {report.versions.map((version) => <Link key={version.report_id} href={`/tools/interior-composer/report/${version.report_id}`} aria-current={version.report_id === report.report_id ? "page" : undefined}>V{String(version.version).padStart(2, "0")}<small>{reportDate(version.created_at)}</small></Link>)}
        </nav>
      </section>

      <footer><p>{report.disclaimer}</p><b>MORROW AI · INTERIOR CONSULTATION REPORT</b></footer>
    </article>
    {message && <p className={styles.toast} role="status">{message}</p>}
  </main>;
}
