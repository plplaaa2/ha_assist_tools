# Assist Tools

[🇺🇸 English Version](./README.md) | [🇰🇷 한국어 버전](./README.ko.md)

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=for-the-badge)](https://github.com/hacs/integration)
![version](https://img.shields.io/badge/version-v0.1.0-blue.svg?style=for-the-badge)
[![Built with Codex](https://img.shields.io/badge/Built%20with-OpenAI%20Codex-412991?style=for-the-badge&logo=openai&logoColor=white)](https://openai.com/codex/)
[![kofi](https://img.shields.io/badge/Ko--fi-Support%20Me-F16061?style=for-the-badge&logo=ko-fi)](https://ko-fi.com/plplaaa2)

HA 음성 도우미가 기본적으로 지원하지 않는 Assist 타이머, 기기 예약 제어, 냉난방/팬 모드 제어를 제공하는 Home Assistant용 HACS 사용자 정의 통합입니다. 

## 기능

- 타이머 설정 및 기기 종료 예약
- 냉난방 및 팬 모드 제어
- 선풍기와 공기청정기의 프리셋, 회전, 방향 설정
- 기기 상태 이력 조회
- 선택한 Home Assistant 날씨 엔티티의 현재 날씨와 최대 7일 일간 예보 조회

## 자동 설치 및 수동 설치

### 자동 설치 (권장)

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?repository=https%3A%2F%2Fgithub.com%2Fplplaaa2%2Fha_assist_tools&category=integrate&owner=plplaaa2)

### 수동 설치

`custom_components/assist_tools/` 폴더 전체를 Home Assistant 설정 폴더의 같은 경로에 복사한 뒤 Home Assistant를 재시작합니다.

## 설정

Assist Tools를 추가할 때 **Assist Timer**를 선택하면 위성 타이머를 설정하고, **Assist Weather**를 선택하면 날씨 엔티티를 연결합니다. 이후 기존 LLM 대화 에이전트의 도구 제공자에서 **Assist Weather**를 활성화하세요. 예보는 선택한 엔티티가 일간 예보를 지원할 때 반환됩니다.

1. **설정 → 기기 및 서비스 → 통합 추가 → Assist Tools**에서 위성을 선택합니다. 표시 이름이 같은 사용 가능한 미디어 플레이어가 하나이면 자동 연결합니다. 일치하는 플레이어를 하나로 정할 수 없으면 플레이어 선택 화면이 표시됩니다. 다른 위성 타이머에서 사용 중인 플레이어는 자동 선택하지 않습니다. 연결한 뒤 재구성에서 플레이어를 변경할 수 있습니다.
2. 위성은 `ANNOUNCE`, 미디어 플레이어는 `PLAY_MEDIA`와 `STOP`을 지원해야 합니다. 위성마다 한 번씩 설정하고, 플레이어를 여러 항목에 중복 연결하지 마세요.
3. 기존 LLM 대화 에이전트의 **Tool providers / Home Assistant API 선택**에서 **Assist Timer**를 활성화합니다. 메뉴 이름은 대화 에이전트마다 다를 수 있으며, 새 대화 에이전트로 바꿀 필요는 없습니다.
4. 테스트 음성 파이프라인에서 **Prefer handling commands locally**를 끕니다. 이 옵션이 켜져 있으면 Home Assistant 내장 에이전트가 타이머 명령을 먼저 처리해 이 provider가 호출되지 않을 수 있습니다.
5. 내장 Assist API도 선택하면 다른 기기 제어가 가능하지만 내장 타이머 도구도 함께 노출될 수 있습니다. 먼저 Assist Timer만 선택해 타이머 도구 호출을 확인하세요.
6. 위성과 미디어 플레이어가 Home Assistant 내부 URL에 접근할 수 있어야 합니다. Home Assistant 네트워크 설정에서 내부 URL을 확인하세요.

일반 미디어 재생은 선택한 플레이어에서 재생 중인 미디어를 대체할 수 있습니다. 원본 MP3는 보존하고, 이를 반복한 `alarm_60s.mp3`를 재생합니다. 재생은 60초로 제한되며 자동 정지도 예약합니다. 공개 정적 URL `/assist_tools/sounds/alarm_60s.mp3`에서는 이 알람 파일 하나만 제공합니다.

## 기여자

- [plplaaa2](https://github.com/plplaaa2) — 프로젝트 소유자 및 유지관리자
- [OpenAI Codex](https://openai.com/codex/) — AI 코딩 도우미
