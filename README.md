# Assist Timers

Home Assistant용 HACS 사용자 정의 통합 구성요소의 개발 골격입니다.

- 버전: `0.1.0`
- 도메인: `assist_timers`
- 상태: 기본 파일 구조만 생성한 초기 개발 단계

## 현재 범위

Home Assistant의 최소 통합 파일 구성과 HACS 저장소 구조를 마련했습니다.
타이머 기능, 엔티티, 서비스 및 설정 화면은 아직 구현하지 않았습니다.
현재 상태에서는 설정 화면에서 추가하거나 타이머를 사용할 수 없습니다.
`const.py`는 도메인 상수를 정의하고, 버전은 `manifest.json`에서 관리합니다.

## 개발 및 배포 전 준비

1. `manifest.json`의 `YOUR_GITHUB_USERNAME`을 실제 저장소 소유자 또는 조직명으로 교체하고 저장소 주소를 확인합니다.
2. `codeowners`에 실제 유지관리자의 GitHub 사용자명(`@사용자명`)을 등록합니다.
3. 기능과 지원 Home Assistant 버전을 정한 뒤 로직 변경 계획을 승인받습니다.
4. 필요한 경우 `config_flow.py`, `strings.json`, `translations/ko.json`, 플랫폼 파일 및 `services.yaml`을 추가합니다. 설정 흐름 구현 시에만 manifest에 `config_flow: true`를 설정합니다.
5. Home Assistant 실행 환경에서 검증하고 HACS 검증을 수행합니다. HACS 등록 시 공식 문서의 브랜드 이미지 요건도 확인합니다.
6. 공개 GitHub 저장소의 설명과 주제를 설정하고, 버전별 배포가 필요하면 `0.1.0` GitHub Release를 발행합니다.

설치 가능한 구현이 완성되면 HACS 사용자 정의 저장소에 공개 저장소 URL을 **Integration** 유형으로 등록합니다.
수동 설치 시에는 `custom_components/assist_timers/`를 Home Assistant 설정 폴더의 같은 경로로 복사합니다.
설치 후 재시작 및 설정 방법은 구현 완료 시 보완합니다.

## 개발 관리

- `tree.md`: 영문 파일 구조 및 역할
- `changelog.jsonl`: 영문 내부 개발 이력
- `caution.jsonl`: 영문 주의사항
- 소스와 문서는 UTF-8(BOM 없음)으로 유지합니다.
- Git은 로컬 `dev` 브랜치부터 관리합니다. 원격 저장소 연결 전에는 push할 수 없습니다.
- 로그 기능을 구현할 때 기본 레벨은 INFO로 하고, 인증 정보를 기록하지 않습니다.

## 공식 개발 문서

- [HACS 통합 요구사항](https://www.hacs.dev/docs/publish/integration/)
- [HACS 공통 요구사항 및 hacs.json](https://hacs.dev/docs/publish/start/)
- [Home Assistant 통합 파일 구조](https://developers.home-assistant.io/docs/creating_integration_file_structure/)
- [Home Assistant manifest 규격](https://developers.home-assistant.io/docs/creating_integration_manifest/)

문서 확인일: 2026-10-14.
