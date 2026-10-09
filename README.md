# Interior Composer

공간 사진에 선택한 가구·마감을 적용하고 Before/After 비교, 품질검사, 버전별 웹 보고서·PDF로 연결하는 **독립 로컬 앱**입니다.

원본 검증 커밋: `4d8ef239519d93bbb33be06f8f3a319df354c236`. 원본 Git 이력·개인 사진·에셋·API 키·보고서는 포함하지 않습니다. 공개 Git 저장소이지, 이미 배포된 웹 서비스는 아닙니다.

## 받는 사람의 시작 순서 — Windows

1. Python 3.12 권장(3.11–3.13 지원 대상), Node.js 22 이상, Git을 설치합니다.
2. 이 저장소를 다운로드하거나 `git clone https://github.com/Lrinvl1203/interior-composer.git`으로 받습니다.
3. 압축을 풀거나 clone한 폴더에서 **install.bat**을 실행합니다. 가상환경·npm 패키지를 설치하고, 없는 경우에만 빈 키의 `.env`를 만듭니다. 인터넷 연결이 필요합니다.
4. `.env`의 `ARK_API_KEY`에 **본인 BytePlus ModelArk 키**를 입력합니다. 계정에서 이미지 모델과 시각 검수 모델의 API 접근 권한도 활성화되어 있어야 합니다. 키는 채팅·Git에 올리지 않습니다.
5. **start-composer.bat** 실행 → <http://127.0.0.1:3100/tools/interior-composer>. BytePlus 키만 설정했다면 화면의 **After 생성 모델을 Flash로 선택**합니다.
6. 끝나면 **stop-composer.bat**. 상태는 **status-composer.bat**. 선택적으로 **install-desktop-icons.bat**으로 START/STOP 아이콘을 설치합니다.

Premium을 사용하려면 별도로 `OPENAI_API_KEY`를 설정합니다. 키 변경 뒤에는 서버를 종료하고 다시 시작합니다. API 키 없이 화면·사진 업로드·선택·작업 저장은 가능하지만 **AI 생성·검수는 동작하지 않습니다**. 다른 앱의 로그인/Codex 캐시를 몰래 사용하는 우회 경로는 없습니다.

기본 포트는 웹 3100·API 18877입니다. 포트 충돌 시 다른 앱을 종료하지 않습니다. 필요한 경우 루트 `.env`의 `INTERIOR_FRONTEND_PORT`·`INTERIOR_BACKEND_PORT`를 바꾸고 재시작하세요. 프로세스 환경변수 설정이 `.env`보다 우선합니다. 프로덕션 빌드를 쓰면서 API 포트를 바꾸면 다시 빌드해야 합니다.

## 사용 흐름

- 에셋: 사진에서 박스와 설명으로 AI 누끼 추출 → 저장·선택. 새 설치의 가구 라이브러리는 비어 있습니다.
- 마감: 네 종류의 마감 중 샘플 선택 → 적용 면 지정. 기본 12개는 직접 만든 설명용 스와치이며 실제 공급사 제품 사진이 아닙니다.
- Before: 공간 사진 업로드 → 가구를 놓을 위치/크기 지정 → 추가 요청 작성.
- After: Flash/Premium 선택 → 확인창 → 생성·좌표 정렬·시각검수. 실패/미확인 결과와 검수 완료본을 구분합니다.
- 보고서: 결과와 생성 당시 선택을 고정해 웹/PDF 버전 저장. 링크는 **같은 서버에 접근할 수 있을 때만** 열립니다. 상대방에게 납품할 때는 PDF를 전달하세요.

마감만 선택하면 기존 가구를 유지하는 요청으로 생성합니다. AI가 실제 상품·구조·픽셀을 완전히 보존한다고 보장하지 않으며 납품 전에 사람이 확인해야 합니다. 검수에는 별도의 시각 모델 호출이 들어가고, 보정이 필요하면 이미지 생성이 추가될 수 있습니다. **무료 여부와 실제 청구는 공급자 계정에서 확인**하세요. STOP은 외부 AI 요청 취소·환불을 보장하지 않습니다.

## 선택 기능 — SAM

기본 설치에는 대형 torch/SAM 모델이 포함되지 않습니다. 원본 픽셀 중심 SAM 추출이 필요할 때만 가상환경에서 `python -m pip install -r requirements-sam.txt`를 실행하고, SAM 2.1 Small 체크포인트를 `room-asset-composer/sam2.1_s.pt`에 준비하세요. 모델·패키지 이용 조건을 먼저 확인하세요. AI 추출과 SAM 설치는 별개입니다.

## 개발·검증

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe scripts/audit_public_tree.py
cd frontend
npm ci
npm run typecheck
npm test
npm run build
npm audit
```

Windows 설치/시작/종료는 런처를 사용하세요. macOS/Linux 개발자는 `python3 -m venv .venv`, `pip install -r requirements.txt`, `uvicorn webapp.server:app --host 127.0.0.1 --port 18877`과 별도 터미널의 `npm run dev -- --hostname 127.0.0.1 --port 3100`을 사용할 수 있습니다. 한글 PDF에는 NanumGothic 등 시스템 한글 폰트가 필요합니다. Windows 맑은 고딕 파일은 재배포하지 않습니다.

프로덕션 실행은 `frontend`에서 `npm run build` 후 `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/launcher.ps1 -Action Start -Production`입니다. 기본 BAT는 로컬 개발 모드입니다.

## 범위와 데이터

포함: 가구 추출·라이브러리, 마감 선택, 사진 부분 합성, 검수·수동 확인, 보고서/PDF, 브라우저 초안 복원·동시 탭 충돌 방지, 작업 상태 복구.

미포함: CAD 도면 인식, Blender/3D 편집, 전체 Morrow 상담 여정, 영상/Seedance 제작, 미래 화면 목업. 이 앱에 그 기능이 연결되어 있다고 표현하지 마세요.

초안은 **브라우저 IndexedDB**, 생성물·에셋·보고서·작업 기록은 **이 설치 폴더**에 저장됩니다. 다른 컴퓨터로 소스만 clone하면 예전 작업은 이동하지 않습니다. 사용자 데이터를 이전하려면 별도로 선별 백업해야 합니다. 런처·테스트는 사용자 자료를 삭제하지 않습니다.

서비스는 `127.0.0.1`에만 바인딩합니다. 로그인·사용자별 권한·운영 큐가 없는 단일 사용자 로컬 앱이므로 인터넷에 그대로 공개하거나 포트포워딩하지 마세요. 공개 저장소를 만든 것과 공개 서비스 운영은 다릅니다.

[인계·개발 기준](PROJECT_CONTEXT.md) · [검증 기록](docs/VERIFICATION.md) · [권리·외부 의존성 안내](NOTICE.md)
