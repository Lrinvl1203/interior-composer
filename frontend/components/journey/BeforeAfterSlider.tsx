"use client";

import { useRef, useState } from "react";

export default function BeforeAfterSlider({
  before,
  after,
  beforeLabel = "Before",
  afterLabel = "After",
}: {
  before: string;
  after: string;
  beforeLabel?: string;
  afterLabel?: string;
}) {
  const frame = useRef<HTMLDivElement>(null);
  const [split, setSplit] = useState(50);

  const updateFromPointer = (clientX: number) => {
    const rect = frame.current?.getBoundingClientRect();
    if (!rect) return;
    setSplit(Math.max(0, Math.min(100, ((clientX - rect.left) / rect.width) * 100)));
  };

  return <div ref={frame} className="relative aspect-[16/10] overflow-hidden rounded-[8px] border border-border bg-sand select-none touch-none" onPointerDown={(event) => { event.currentTarget.setPointerCapture(event.pointerId); updateFromPointer(event.clientX); }} onPointerMove={(event) => { if (event.currentTarget.hasPointerCapture(event.pointerId)) updateFromPointer(event.clientX); }}>
    <img src={before} alt={beforeLabel} className="absolute inset-0 h-full w-full object-contain saturate-90" />
    <div className="absolute inset-0 overflow-hidden motion-reduce:transition-none" style={{ clipPath: `inset(0 0 0 ${split}%)` }}><img src={after} alt={afterLabel} className="h-full w-full object-contain saturate-90" /></div>
    <span aria-hidden="true" className="pointer-events-none absolute inset-y-0 w-px bg-cream" style={{ left: `${split}%` }} />
    <span aria-hidden="true" className="pointer-events-none absolute top-1/2 grid h-12 w-12 -translate-x-1/2 -translate-y-1/2 place-items-center rounded-full border-2 border-cream bg-mustard font-heading text-lg text-cream shadow-[var(--shadow-card)]" style={{ left: `${split}%` }}>↔</span>
    <span className="absolute left-4 top-4 bg-ink/80 px-3 py-1.5 text-xs font-semibold text-cream">{beforeLabel}</span>
    <span className="absolute right-4 top-4 bg-ink/80 px-3 py-1.5 text-xs font-semibold text-cream">{afterLabel}</span>
    <input aria-label={`${beforeLabel}와 ${afterLabel} 비교`} type="range" min="0" max="100" value={split} onChange={(event) => setSplit(Number(event.target.value))} className="absolute inset-0 h-full w-full cursor-ew-resize opacity-0" />
  </div>;
}
