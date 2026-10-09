# Interior Composer

공간 사진에 가구·마감을 적용하고 Before/After 비교, 검수, 버전별 웹 보고서·PDF로 연결하는 **독립 로컬 앱**입니다. 모든 유료 AI 경로는 OpenAI이며 키 하나로 연결합니다. API 키 없이도 먼저 설치하고 화면을 사용할 수 있습니다.

## 컴퓨터를 잘 모르는 받는 사람

자기 Windows PC의 Codex 앱/CLI에 **아래 문장만** 붙여넣으세요.

> https://github.com/Lrinvl1203/interior-composer 의 CODEX_INSTALL.md를 읽고 이 PC에 필요한 프로그램 설치부터 바탕화면 시작·종료 아이콘과 실행 확인까지 전부 해줘. API 키는 나중에 설정할 거니까 지금은 묻지 마.

[Codex 자동 설치 안내](CODEX_INSTALL.md)에 필요한 절차가 담겨 있습니다. Git·Python·Node를 직접 설치할 필요가 없도록 자동 설치가 준비합니다. 로컬 실행 권한 확인 창이 뜨면 승인만 필요할 수 있습니다. 기본 지원 대상은 Windows x64이며 인터넷 연결이 필요합니다.

## 설치 스크립트를 직접 쓰는 경우

소스를 다운로드한 폴더에서 아래를 실행하면 런타임·패키지·빌드·서버까지 준비합니다.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/bootstrap.ps1 -CreateDesktopIcons
```

기본 웹 주소는 <http://127.0.0.1:3100/tools/interior-composer>. 사용 중인 포트가 있으면 다른 앱을 종료하지 않고 이 앱의 다른 포트를 선택합니다. **실행 결과에 표시한 주소**를 사용하세요. 이후에는 **start-composer.bat / stop-composer.bat** 또는 바탕화면 START/STOP 아이콘을 사용합니다.

기술 사용자는 기존 `install.bat`도 사용할 수 있습니다. 모든 다운로드 런타임은 프로젝트 `.local/`에 두며 전역 PATH나 관리자 설치가 필요하지 않습니다. 재설치할 때 기존 `.env`·사진·에셋·작업 결과는 덮어쓰지 않습니다.

## API는 나중에 연결

처음에는 키가 비어 있고 AI 생성/추출은 막혀 있습니다. 사진 업로드·마감 선택·배치 설정·초안 저장은 가능하며 가상의 결과를 실제 AI 결과처럼 표시하지 않습니다.

후속 설정은 루트 `.env`의 **OPENAI_API_KEY 하나**입니다. 현재 이미지 모델 기본값은 `gpt-image-2`, 시각 검수 기본값은 `gpt-5-mini`이며 설정으로 변경할 수 있습니다. 계정에서 모델 권한을 확인한 뒤 서버를 재시작합니다. 계정 가입·결제·API 키 설정은 자동 설치 과정에서 요구하지 않습니다. ChatGPT/Codex 로그인으로 API 요금을 대신하거나 자동 인증을 가져오지 않습니다.

“빠르게”와 “정밀하게”는 **같은 OpenAI 이미지 모델의 품질 설정**입니다. 가구 AI 추출도 OpenAI, 시각 검수도 OpenAI를 사용하며 BytePlus는 필요 없습니다. 검수와 필요한 보정에 추가 사용량이 발생할 수 있습니다. 실제 청구는 계정에서 확인하고, STOP이 외부 API 요청 취소/환불을 보장한다고 해석하지 마세요.

[OpenAI 이미지 생성 문서](https://developers.openai.com/api/docs/guides/image-generation) · [시각 검수 모델](https://developers.openai.com/api/docs/models/gpt-5-mini) · [구조화 응답](https://developers.openai.com/api/docs/guides/structured-outputs)

## 사용 흐름과 한계

- 사진에서 박스·설명으로 가구를 AI 추출하고 라이브러리로 저장합니다. 새 설치의 가구 라이브러리는 비어 있습니다.
- 기본 12개 마감은 직접 만든 설명용 스와치이며 실제 공급사 제품 사진/성능 보증이 아닙니다.
- 공간 사진과 적용 면·배치·추가 요청을 준비하고, 키를 설정한 뒤 생성합니다.
- 결과와 생성 당시 입력을 고정해 웹 보고서/PDF로 저장합니다. 보고서 링크는 **같은 서버에 접근할 수 있을 때만** 열립니다. 외부 납품에는 PDF를 전달하세요.

AI가 실제 상품·구조·픽셀을 완전히 보존한다고 보장하지 않습니다. 납품 전에 사람이 확인하세요. 2D 구도 정렬을 3D 구조 검증으로 표현하지 않습니다.

SAM은 생성 API가 아니라 선택적인 로컬 마스킹 기능입니다. 기본 자동 설치에 큰 torch/SAM 가중치는 포함하지 않습니다. 원할 때만 `requirements-sam.txt` 및 해당 모델 조건을 확인해 별도로 설치하세요.

## 개발·검증

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe scripts/audit_public_tree.py
cd frontend
npm run typecheck
npm test
npm run build
npm audit
```

오프라인 브라우저 fixture 재현은 [검증 기록](docs/VERIFICATION.md)을 참고하세요. fixture는 실제 AI 검증 증거가 아닙니다. 유료 호출 없는 테스트가 기본입니다.

macOS/Linux 개발 수동 실행: `python3 -m venv .venv`, `pip install -r requirements.txt`, `uvicorn webapp.server:app --host 127.0.0.1 --port 18877`와 별도 `frontend` 터미널의 `npm run dev -- --hostname 127.0.0.1 --port 3100`. 한글 PDF에는 시스템 NanumGothic 등 한글 폰트가 필요합니다. Windows 폰트 파일은 재배포하지 않습니다.

## 범위·데이터·인계

원본 검증 커밋은 `4d8ef239519d93bbb33be06f8f3a319df354c236`이며, 이후 독립 공급자/설치 개선은 새 Git 이력으로 기록합니다. 원본 개인 데이터·API 키·Git 이력은 포함하지 않습니다.

포함: 가구 추출/선택·마감·사진 합성·검수·보고서/PDF·브라우저 초안과 두 탭 충돌 방지·작업 복구. 미포함: CAD·Blender/3D·전체 Morrow 상담 여정·영상/Seedance·미래 목업.

초안은 브라우저 IndexedDB, 생성물·보고서·에셋은 이 설치 폴더에 저장됩니다. 소스만 받으면 과거 작업은 이동하지 않으며 사용자 자료는 별도 선별 백업이 필요합니다. 인터넷에 인증 없이 공개하거나 포트포워딩하지 마세요. 공개 저장소와 공개 서비스 운영은 다릅니다.

[개발 인계](PROJECT_CONTEXT.md) · [권리/외부 의존성](NOTICE.md)
