# 독립 분리본 검증 — 2026-10-10

원본 검증 커밋 `4d8ef239519d93bbb33be06f8f3a319df354c236`에서 필요한 코드만 추출했다. 원본 이력·인증·고객 데이터는 복사하지 않았다. 원본 작업 트리와 기존 3000/18777 서버는 유지했다.

## 로컬 완료 검사

- 별도 새 Python 3.13 가상환경: backend **120개 통과**, 심볼릭 링크 생성 권한 관련 1개 건너뜀, 기존 Starlette 경고 1개.
- 원본 Composer/품질/ModelArk 75개 회귀 검사를 변경 없이 포함한다. 추가 검사는 빈 설치·키 미설정·잡 복구·경로 제한·Origin 검사·안전한 오류 메시지다.
- Frontend **32개 통과**(Composer 저장 관련 테스트만 추출), TypeScript 및 Next.js 프로덕션 빌드 통과.
- npm audit / 새 환경 pip-audit: 알려진 취약점 0건. 처음 검사에서 발견한 Next.js/Vitest 및 Python 설치기의 pip 문제는 분리본에서 수정했다. 모든 미래 취약점 부재를 보장하는 의미는 아니다.
- 실제 로컬 프록시를 거치는 Playwright **12개 검사 통과**: 초기 상태, 확인창/Escape, 바이너리 사진 업로드, 가상 공급자 생성, 보고서 V1/V2, 생성 당시 요청 보존, 새로고침 복원, PDF 다운로드, 독립 보고서, 여러 탭 충돌 방지·복구, 320/768/1440px 가로 넘침, 페이지 오류 없음.
- 실제 프로덕션 서버에서 키 미설정 시 `ARK_API_KEY`와 `.env` 설정 안내가 표시된다. 원본의 Codex 인증 우회는 없다.
- 스모크의 이미지 생성과 시각 검수는 **명시적인 오프라인 fixture**이다. 추가 실제 AI 과금 호출은 0회이며, 이식본의 실제 공급자 생성 품질을 새로 검증했다고 주장하지 않는다.

## 재현 가능한 브라우저 스모크

의존성 설치 후 터미널 세 개를 사용한다. 테스트 사진·PDF·로그는 `.local/`에만 저장된다. 실제 공급자 서버에서는 검사 스크립트가 실행을 거부한다.

```powershell
# 터미널 1 — 가상 공급자, 외부 AI HTTP 차단
.\.venv\Scripts\python.exe scripts/smoke_fixture_server.py --allow-offline-fixtures

# 터미널 2 — 가상 API를 가리키는 별도 프런트
cd frontend
$env:INTERIOR_BACKEND_PORT='18879'
$env:INTERIOR_FRONTEND_PORT='3102'
npm run dev -- --hostname 127.0.0.1 --port 3102

# 터미널 3 — 프로젝트 루트
node scripts/browser-smoke.mjs
```

## 경계

대형 SAM 모델·가중치는 기본 설치/검증 범위가 아니다. 한국어 PDF는 시스템 폰트를 사용한다. 브라우저 검사는 Chromium이며 실기기 전체를 검증한 것은 아니다. 공개 저장소만 생성하며 인터넷 서비스 배포·포트포워딩·사용자 데이터 업로드는 하지 않는다.

별도 clone의 설치 BAT·시작/종료 및 원본과 무관한 실행 검증 결과는 인계 완료 전에 이 기록에 추가한다.
