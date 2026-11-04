# PuriPuly Heart — 휴대용 버전

자체완결형 빌드: 설치된 Python이 필요 없고 폴더 밖에 아무것도 남기지 않습니다.
폴더는 다른 드라이브나 컴퓨터로 옮길 수 있습니다 — 실행은 폴더 위치 기준으로
동작하며 폴더 이름은 어디에도 하드코딩되어 있지 않습니다.

## 실행

`start.exe` — 유일한 실행 파일, 매개변수 없음:

- **실행** — 일반 시작, 추가 창 없음;
- **디버그로 실행** — Python 런타임 콘솔 + 프로그램의 디버그 UI 요소
  (`--debug-ui-preview` 키);
- **유지 관리** — 프로그램 실행 없이 데이터 작업:
  - **사용자 데이터 삭제** — `data_user`의 모든 내용을 삭제;
  - **Python 실행 임시 파일 삭제** — `__pycache__`(컴파일된 코드)를 삭제;
    코드 변경 후 사용하면 프로그램이 오래된 캐시를 따르지 않습니다;
  - **캐시 지우기** — `cache` 폴더의 모든 내용을 삭제
    (재생성 가능한 캐시: 다운로드한 패키지, 클라이언트 캐시, 임시 파일;
    자동으로 다시 생성됩니다);
- **종료**.

설명은 마우스를 올리면 표시됩니다. 런처 인터페이스 언어는 Windows 언어를
따르며 35개 언어가 내장되어 있습니다.

참고: 일반 시작 후 프로그램이 오류로 종료되면 런처가 오류 코드 대화 상자를
표시합니다(진단하려면 디버그 사용). 오류가 있는 정리 결과는 일부 파일이
사용 중임을 의미합니다 — 프로그램을 닫고 다시 시도하세요. 런처 창은 한 번에
하나만 열 수 있습니다.

비밀과 API 키는 `data_user/`에 평문으로 저장됩니다 — 암호화는 의도적으로
사용하지 않습니다.

## 구조

- `start.exe`, `start.cs` — 런처와 그 소스 (UTF-8)
- `src/` — 프로그램 코드 (소스에서 직접 실행)
- `python/` — 휴대용 Python 3.14 + 모든 종속성 (`Lib/site-packages`)
- `build/` — 네이티브 인식 런타임: ASR 워커, transcribe.cpp, ggml 백엔드
- `data_user/` — 모든 사용자 데이터: 설정, 모델, 비밀, 로그
- `cache/` — 재생성 가능한 캐시와 임시 파일 (삭제해도 됨)
- `prompts/` — 프롬프트 템플릿 (런타임 데이터)
- `third_party/` — 라이선스 고지, 글꼴, OpenVR
- `native/` — Rust 구성 요소 소스 (오버레이 등)
- `scripts/`, `pyproject.toml`, `uv.lock`, `LICENSE` — 빌드 스크립트, 종속성 매니페스트, 라이선스
- `tests/` — 테스트
- `docs/` — added/deleted 기록 (세 가지 언어)

## 휴대성과 격리

- 인터프리터와 모든 종속성은 `python/`에 있습니다. 시스템 Python은 사용되지
  않으며 변경되지 않습니다.
- `python/python314._pth`이 경로를 지정합니다: 표준 라이브러리, 자체
  `Lib/site-packages`, 소스 `../src`. 외부 환경 변수(`PYTHONPATH`,
  `PYTHONHOME`)는 무시되며 사용자 site-packages는 혼입되지 않습니다.
- 레지스트리, PATH, 파일 연결은 변경되지 않습니다.
- 애플리케이션 데이터는 항상 폴더 내부의 `data_user/`에 있습니다.
- 참고: 재생성 가능한 모든 캐시(pip, uv, Flet 클라이언트, Hugging Face, 임시 파일)는
  폴더 내부의 `cache/`에 있으며 `data_user/`에는 사용자 데이터만 있습니다.

## 패키지 관리

패키지는 휴대용 인터프리터로만 관리합니다:

```
python\python.exe -m pip install <패키지>
python\python.exe -m pip install -U <패키지>
python\python.exe -m pip uninstall <패키지>
```

모든 내용이 폴더 안에 남습니다: 패키지 코드와 메타데이터는
`python/Lib/site-packages`, 진입점 셈은 `python/Scripts`, 다운로드 캐시는
`cache/pip`에 있습니다. 인터프리터는 시스템 Python 위치에 영향을 주지 않습니다.
`python/INSTALLED_PACKAGES.txt`에 제공 패키지 목록이 있습니다.

## 런처 다시 빌드

```
%SystemRoot%\Microsoft.NET\Framework64\v4.0.30319\csc.exe /nologo /codepage:65001 /target:winexe /platform:anycpu /optimize+ /r:System.dll /r:System.Drawing.dll /r:System.Windows.Forms.dll /r:System.Core.dll /win32icon:src\puripuly_heart\data\icons\icon.ico /out:start.exe start.cs
```

`/codepage:65001` 플래그는 필수입니다: 소스는 UTF-8입니다(35개 언어).
