import InteriorConsultationReport from "@/components/InteriorConsultationReport";

export default async function InteriorComposerReportPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <InteriorConsultationReport reportId={id} />;
}
