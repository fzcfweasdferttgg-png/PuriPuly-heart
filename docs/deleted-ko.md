# 삭제된 기능

저자 원본 대비 휴대용 버전에서 삭제된 기능과 함께 제거된 패키지 의존성입니다.

## 관리형 컨트롤 플레인(broker)

broker 서비스(Cloudflare Worker), 배포, 마이그레이션, 테스트.
의존성: 없음 (Node/pnpm 도구 세트가 함께 제거됨).

## 설치 프로그램과 빌드

Inno Setup 설치 프로그램, 설치 시 모델 프로비저닝, build spec.
의존성: 없음.

## 빌드/CI 도구

GitHub CI, 스크립트, pnpm 매니페스트, tsconfig, vitest, Rust 툴체인 설정.
의존성: 없음.

## 저자 문서

AGENTS.md, CODE_SIGNING.md, PRIVACY.md, README.md.
의존성: 없음.

## 외부 패키지 캐시

프로그램 폴더 외부의 pip 및 uv 캐시.
의존성: 없음.

## 클라우드 음성 인식

STT 제공자 Deepgram, Gemini Transcribe, ElevenLabs Scribe와 해당 API 키 확인,
언어 매핑, 무료 요금제 간 순환(프로바이더 어댑터 모듈 제거됨).
의존성: deepgram-sdk, elevenlabs, google-genai, google-auth, pyasn1,
pyasn1-modules, distro, tenacity.

## 클라우드 번역 제공자(Gemini)

LLM 제공자 Gemini와 API 키 확인(프로바이더 어댑터 모듈 제거됨).
의존성: google-genai(클라우드 음성 인식과 함께 제거됨).

## 비밀 암호화

Fernet 암호화 비밀 저장소, Windows Credential Manager(keyring) 저장소,
계정 로그인의 RSA ID 토큰 검증; 이제 비밀은 평문으로 저장됩니다.
의존성: cryptography, keyring, pywin32-ctypes, jaraco.classes,
jaraco.context, jaraco.functools, more-itertools.

## 클라우드 번역 제공자

DeepSeek, Qwen(DashScope), OpenAI, OpenRouter, ChatGPT 백엔드, 공개
index-translate API: 제공자 어댑터 모듈이 제거되었습니다. 번역은 로컬
OpenAI 호환 엔드포인트 또는 로컬 Gemma 런타임으로 동작합니다.
의존성: 없음(코드만).

## 유료 클라우드 음성 인식

Soniox(실시간), Qwen Audio(DashScope), 사용자 지정 STT 연결과 이들의 API 키
검증이 제거되었습니다. 로컬 CPU 인식 제공자도 제거되어 인식은 단일 로컬
Qwen3 ASR 제공자(transcribe.cpp)로만 남습니다.
의존성: 없음(코드만).

## 계정 로그인과 managed 키

QQ 로그인, Discord OAuth, ChatGPT OAuth, OpenRouter PKCE OAuth와 브로커를
통한 managed 키 발급, managed identity 서명이 제거되었습니다. 계정 및 로그인
카드는 설정 인터페이스에서 사라졌습니다.
의존성: 없음(코드만).

## 일 단위 활동 텔레메트리

브로커로의 일 단위 활동 핑과 설정 마이그레이션의 번역 성공 날짜 기록이
제거되었습니다.
의존성: 없음(코드만).

## 저자의 ASR 모델 카탈로그

저자의 ASR 모델(orukeet, fun-asr nano/mlt, 일본어 parakeet sherpa,
qwen3-asr-1.7b Q6_K)과 sha256이 고정된 매니페스트가 사용자 GGUF 카탈로그와
간소화된 매니페스트로 교체되었습니다.
의존성: 없음(코드만).

## sherpa/ONNX 인식 경로

sherpa-onnx 인식 백엔드(일본어 parakeet), 그 Windows 런타임 부트스트랩과
런타임 점검 명령이 제거되었습니다.
의존성: sherpa-onnx, sherpa-onnx-core.

## 설정 인터페이스 정리

제거된 기능들의 카드와 컨트롤이 설정 인터페이스에서 삭제되었습니다: ChatGPT
계정 카드, 체험 기간 표시가 붙은 managed 키 카드, 사용자 지정 STT 연결
카드, 열 개의 클라우드 키 필드가 있는 API 키 섹션, Qwen 지역과 API 호스트
설정, 텔레메트리 토글, OpenRouter PKCE 버튼. 남은 카드(Local LLM, GPU
장치, ASR 모델, HTTP 확장)는 해당 설정이 선택되었을 때만 표시됩니다.
의존성: 없음(인터페이스만).

## 홍보 표면

GitHub 별표 스낵바와 시작 후 표시 흐름, 창립자 편지 다이얼로그와 웜 문서
트리거, API 키 안내 버튼, 이벤트 파이프라인의 번역 성공 홍보 훅이
제거되었습니다.
의존성: 없음(인터페이스만).
