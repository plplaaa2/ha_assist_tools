# HA Assist Tools

[🇺🇸 English Version](./README.md) | [🇰🇷 한국어 버전](./README.ko.md)

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=for-the-badge)](https://github.com/hacs/integration)
![version](https://img.shields.io/badge/version-v0.3.4-blue.svg?style=for-the-badge)
[![Built with Codex](https://img.shields.io/badge/Built%20with-OpenAI%20Codex-412991?style=for-the-badge&logo=openai&logoColor=white)](https://openai.com/codex/)
[![kofi](https://img.shields.io/badge/Ko--fi-Support%20Me-F16061?style=for-the-badge&logo=ko-fi)](https://ko-fi.com/plplaaa2)

HA 음성 도우미가 기본적으로 지원하지 않는 Assist 타이머, 기기 예약 제어, 냉난방/팬 모드 제어를 제공하는 Home Assistant용 HACS 사용자 정의 통합입니다. 

## 기능

- Assist에서 직접적인 구조 요청을 인식하면 지정한 Home Assistant Companion 앱으로 알림 전송
- 타이머 설정 및 기기 종료 예약
- Assist 음성으로 예약을 조회·변경·취소하고, 원하는 요일에 기기 동작을 반복 예약
- 원하는 시각에 한 번 또는 여러 요일에 반복해서 울리는 알람
- 냉난방 및 팬 모드 제어
- 선풍기와 공기청정기의 프리셋, 회전, 방향 설정
- 기기 상태 이력 조회
- 선택한 Home Assistant 날씨 엔티티의 현재 날씨와 최대 7일 일간 예보 조회

## 자동 설치 및 수동 설치

### 자동 설치 (권장)

[![Open your Home Assistant instance and open a repository in the Marketplace.](https://my.home-assistant.io/badges/marketplace_repository.svg)](https://my.home-assistant.io/redirect/marketplace_repository/?owner=plplaaa2&repository=ha_assist_tools&category=integration)

### 수동 설치

`custom_components/assist_tools/` 폴더 전체를 Home Assistant 설정 폴더의 같은 경로에 복사한 뒤 Home Assistant를 재시작합니다.

## 설정

Assist Tools를 추가할 때 **Assist Timer**, **Assist Weather**, **Assist Emergency Contact** 등 원하는 기능을 선택하세요. 각 기능을 기존 LLM 대화 에이전트에서 별도 도구 제공자로 활성화하세요.

### Assist Timer 설정

1. **설정 → 기기 및 서비스 → 통합 추가 → Assist Tools**에서 위성을 선택합니다. 표시 이름이 같은 사용 가능한 미디어 플레이어가 하나이면 자동 연결합니다. 일치하는 플레이어를 하나로 정할 수 없으면 플레이어 선택 화면이 표시됩니다. 다른 위성 타이머에서 사용 중인 플레이어는 자동 선택하지 않습니다. 연결한 뒤 재구성에서 플레이어를 변경할 수 있습니다.
2. 위성은 `ANNOUNCE`, 미디어 플레이어는 `PLAY_MEDIA`와 `STOP`을 지원해야 합니다. 위성마다 한 번씩 설정하고, 플레이어를 여러 항목에 중복 연결하지 마세요.
3. 기존 LLM 대화 에이전트의 **Tool providers / Home Assistant API 선택**에서 **Assist Timer**를 활성화합니다. 메뉴 이름은 대화 에이전트마다 다를 수 있으며, 새 대화 에이전트로 바꿀 필요는 없습니다.
4. 테스트 음성 파이프라인에서 **Prefer handling commands locally**를 끕니다. 이 옵션이 켜져 있으면 Home Assistant 내장 에이전트가 타이머 명령을 먼저 처리해 이 provider가 호출되지 않을 수 있습니다.
5. 내장 Assist API도 선택하면 다른 기기 제어가 가능하지만 내장 타이머 도구도 함께 노출될 수 있습니다. 먼저 Assist Timer만 선택해 타이머 도구 호출을 확인하세요.
6. 위성과 미디어 플레이어가 Home Assistant 내부 URL에 접근할 수 있어야 합니다. Home Assistant 네트워크 설정에서 내부 URL을 확인하세요.

일반 미디어 재생은 선택한 플레이어에서 재생 중인 미디어를 대체할 수 있습니다. 원본 MP3는 보존하고, 이를 반복한 `alarm_60s.mp3`를 재생합니다. 재생은 60초로 제한되며 자동 정지도 예약합니다. 공개 정적 URL `/assist_tools/sounds/alarm_60s.mp3`에서는 이 알람 파일 하나만 제공합니다.

**Assist Timer**에서 “내일 오전 7시에 깨워 줘”, “월·수·금 오전 7시에 알람 맞춰 줘”, “다음 알람을 8시로 바꿔 줘”, “알람 소리 꺼 줘”라고 말할 수 있습니다. 지정 시각 알람은 요청을 받은 Assist에 연결된 개별 스피커에서 울립니다. 텍스트 요청에 스피커 후보가 여러 개면 대상을 지정하세요. Assist Tools 통합의 설정에서 **타이머 종료음**과 **지정 시각 알람음**을 Home Assistant의 **내 미디어**에서 따로 고를 수 있습니다. 항목을 비워 두면 내장음을 사용합니다.

### Assist + Google Speaker 설정

Assist Tools를 추가할 때 **Assist + Google Speaker**를 선택하고 마이크 전용 Assist 위성과 Google Cast 스피커를 연결합니다. 기존 LLM 대화 에이전트에서 **Assist Timer**와 **Assist Scheduled Control**을 활성화하세요.

**Assist Scheduled Control**에서 “월·수·금 오전 7시에 거실 커튼 열어 줘”, “거실 커튼 예약 알려 줘”, “다음 번만 오전 8시로 옮겨 줘”, “앞으로 수요일은 빼 줘”라고 요청할 수 있습니다. 반복 시각은 Home Assistant 시간대를 따릅니다. 다음 한 번의 실행은 그다음 정규 실행보다 앞선 시각으로 옮길 수 있으며, 반복 예약을 취소하면 이후 실행도 모두 취소됩니다.

타이머 만료음은 Google 스피커에서 재생됩니다. 위성의 알림 기능 없이도 타이머와 예약 제어를 사용할 수 있습니다. 일반 Assist 응답을 같은 스피커에서 듣는 경우 ESPHome의 `on_tts_end`에서 받은 URL을 Home Assistant의 `media_player.play_media`에 전달하세요. Google 스피커는 Home Assistant의 내부 URL에 접근할 수 있어야 합니다.

### Assist 긴급 연락 설정

Assist Tools를 추가할 때 **Assist Emergency Contact**를 선택하고, 수신 기기의
`notify.mobile_app_*` 액션을 고르세요. 기존 LLM 대화 에이전트에서 **Assist Emergency Contact**를
활성화하세요. 수신 기기는 통합의 재구성 메뉴에서 바꿀 수 있습니다.

“살려줘”, “구해줘”, “제발 도와주세요”, “SOS” 같은 직접 요청은 추가 확인 없이
알림을 요청합니다. LLM 대화 에이전트가 언어와 문맥을 판단하므로 인식 결과는 모델에 따라 달라질 수 있습니다.
Assist에서 성공을 안내하면 Home Assistant가 알림 액션을 실행한 상태입니다.
기기에 알림이 표시됐는지는 수신 기기에서 확인하세요.

## 라이선스

프로젝트 코드는 [MIT 라이선스](LICENSE)를 따릅니다. 동봉된 알람 음원과 반복 재생용 파일에는 [Pixabay 원본 음원](https://pixabay.com/sound-effects/beep-alarm-366507/)의 이용 조건이 적용됩니다.

## 기여자

- [plplaaa2](https://github.com/plplaaa2) — 프로젝트 소유자 및 유지관리자
- [OpenAI Codex](https://openai.com/codex/) — AI 코딩 도우미
