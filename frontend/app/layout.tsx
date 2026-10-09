import "./globals.css";
import Link from "next/link";
export const metadata = { title: "Interior Composer", description: "공간 사진에 가구와 마감을 적용하고 검수·보고서로 연결하는 독립 도구" };
export default function Layout({ children }: { children: React.ReactNode }) {
  return <html lang="ko"><body>
    <a className="skip-link" href="#main-content">본문으로 건너뛰기</a>
    <header className="site-header"><div className="shell nav"><Link href="/tools/interior-composer" className="brand">Interior Composer</Link><span>가구·마감 합성과 상담 보고서</span></div></header>
    <div id="main-content">{children}</div>
    <footer className="site-footer"><div className="shell">로컬 작업 도구 · AI 생성은 설정한 계정에서 과금될 수 있습니다. 보고서 링크는 이 서버에 접근할 수 있는 환경에서만 열립니다.</div></footer>
  </body></html>;
}
