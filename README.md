# Assist Tools

Assist 타이머, 기기 예약 제어와 냉난방 모드 제어를 제공하는 Home Assistant용 HACS 사용자 정의 통합입니다.
몇 분 후 기기를 켜거나 끄는 예약 제어도 함께 제공합니다.

- 버전: `0.1.0`
- 도메인: `assist_timers` (기존 설정 호환성을 위해 유지)
- Tool providers: **Assist Timer**(타이머), **Assist Scheduled Control**(기기 켜기·끄기 예약), **Assist Climate**(냉난방·팬 모드), **Assist Fan**(선풍기 프리셋·회전·방향); 각각 선택하여 활성화
- 지원 기준: Home Assistant `2026.10.1` 이상
- 상태: 로컬 구현 및 단위 테스트 완료, 실제 HA·위성 검증 대기

## 현재 범위

기존 LLM 대화 에이전트에서 선택하는 **Assist Timer** Tool provider를 제공합니다.
음성 명령은 호출한 HA 장치 ID로 위성을 찾으며, 다른 위성으로 변경할 수 없습니다.
일반 대화창에도 타이머 도구를 제공합니다. 등록된 위성이 하나면 자동으로 사용합니다.
여러 개면 “주방 위성에 5분 타이머 설정해 줘”처럼 대상을 지정하세요. 대상을 지정하지 않으면 LLM이 어느 위성인지 질문하도록 안내하며, 도구도 임의 선택을 거절합니다.
대화창 요청의 확인 응답은 LLM이 표시하고, 만료 알람은 선택된 위성의 플레이어에서 재생합니다.

각 타이머에 고유 ID를 발급하고 이름을 선택적으로 지정할 수 있습니다.
실행 중·일시정지·알람 재생·오류 상태도 위성의 한 슬롯을 차지합니다.
기존 타이머가 있으면 새 생성을 거절하며, 덮어쓰지 않습니다.
시간 범위는 1초부터 7일까지이고, 일시정지 후 재개하면 남은 시간만 진행합니다.

| 동작 | 재생 방식 및 문구 |
|---|---|
| 5분 설정 | 기존 LLM 에이전트가 설정 결과를 답변; 별도 `announce` 없음 |
| 일시정지 | 위성 `announce`: “설정한 5분 알람을 일시정지하였습니다.” |
| 재개 | 위성 `announce`: “설정한 5분 알람을 재개하였습니다.” |
| 만료 전 취소·중지 | 위성 `announce`: “설정한 5분 알람을 중지하였습니다.” |
| 만료 | 연결한 플레이어에서 MP3를 **일반 미디어**로 60초 재생 |
| 알람 재생 중 취소·중지 | 플레이어 정지 명령 전송, 추가 `announce` 없음; 기존 에이전트의 확인 음성은 허용 |

확인 문구의 시간은 처음 설정한 전체 시간입니다. 확인 음성에는 별도 차임을 넣지 않습니다.
LLM에는 이미 예약된 확인 문구를 반복하지 않도록 안내하지만, 추가 발화는 모델에 따라 나올 수 있습니다.
음성 취소는 STT와 LLM이 취소 도구를 호출한 시점에 플레이어 정지 명령을 보냅니다.
실제 스피커 정지 지연은 네트워크와 장치에 따라 달라집니다.

## 로컬 설치 및 설정

1. `custom_components/assist_timers/` 전체를 HA 설정 폴더의 같은 경로로 복사하고 재시작합니다.
   로컬 설치 ZIP은 HA 설정 폴더에 풀면 위 경로가 만들어집니다. 기존 타이머 통합을 사용 중이라면 덮어쓰기 전에 백업하세요.
2. **설정 → 기기 및 서비스 → 통합 추가 → Assist Tools**에서 위성을 선택합니다. 표시 이름이 같은 사용 가능한 미디어 플레이어가 하나이면 자동으로 연결합니다. 없거나 여러 개이면 플레이어 선택 화면이 나옵니다. 다른 위성 타이머가 사용 중인 플레이어는 자동 선택하지 않습니다. 연결 후에는 재구성에서 변경할 수 있습니다.
3. 위성은 `ANNOUNCE`, 플레이어는 `PLAY_MEDIA`와 `STOP` 기능을 지원해야 합니다. 위성별로 한 번씩 설정하며 플레이어도 다른 항목과 중복 연결할 수 없습니다.
4. 기존 LLM 대화 에이전트의 **Tool providers / Home Assistant API 선택**에서 **Assist Timer**를 활성화합니다. 메뉴 명칭은 대화 에이전트에 따라 다릅니다. 새로운 대화 에이전트로 바꿀 필요는 없습니다.
5. 테스트용 음성 파이프라인에서 **Prefer handling commands locally**를 끕니다. 켜면 HA 내장 에이전트가 타이머 명령을 먼저 처리하여 이 도구를 호출하지 않을 수 있습니다.
6. 내장 Assist API를 함께 선택하면 다른 기기 제어도 가능하지만 내장 타이머 도구도 제공될 수 있습니다. 우선 Assist Timer만 선택해 타이머 도구 호출을 검증하세요.
7. 위성과 미디어 플레이어에서 HA의 내부 URL에 접근할 수 있어야 합니다. HA 네트워크 설정의 내부 URL을 확인하세요.

일반 미디어 재생은 해당 플레이어의 현재 미디어를 대체할 수 있습니다.
원본 MP3는 보존하고, 이를 반복한 `alarm_60s.mp3`를 재생합니다.
재생 파일 자체가 60초로 제한되며 자동 정지 예약도 설정합니다.
공개 정적 URL `/assist_timers/sounds/alarm_60s.mp3`에는 이 알람 파일 하나만 제공합니다.
HACS 공개 등록은 아직 하지 않았습니다. 로컬 ZIP은 수동 설치용이며 HACS 릴리스 ZIP 설정은 사용하지 않습니다.

## 선풍기·공기청정기 프리셋·회전·방향 설정

기존 LLM 대화 에이전트에서 **Assist Fan** Tool provider를 활성화합니다.
통합 갱신 후 HA를 재시작하고, 대상 `fan` 엔티티를 Assist에 노출하세요.

`fan.*`로 등록된 공기청정기도 같은 provider에서 지원합니다.
“거실 공기청정기를 자동 모드로 바꿔 줘”, “공기청정기를 수면 모드로 바꿔 줘”처럼
요청할 수 있으며, 해당 기기의 `preset_modes`에 실제 제공되는 값만 사용합니다.
회전·방향 기능이 없는 공기청정기에는 프리셋 도구만 제공합니다.
`fan.*`가 아닌 다른 엔티티로 제공되는 기기는 이 provider의 대상이 아닙니다.

- `AssistFanSetPresetMode`: 기기의 `preset_modes`에 있는 프리셋 설정
- `AssistFanSetOscillation`: 지원하는 기기의 좌우 회전 켜기·끄기
- `AssistFanSetDirection`: 지원하는 기기의 날개 정방향(`forward`)·역방향(`reverse`) 설정

“선풍기를 수면 모드로 바꿔 줘”, “선풍기 좌우 회전 꺼 줘”,
“실링팬을 역방향으로 바꿔 줘”처럼 요청합니다. 프리셋은 기기의 실제 값에
대응해야 하며, 모호한 대상·프리셋·회전 요청은 질문하도록 안내합니다.
좌우 회전과 날개 회전 방향은 별개 기능입니다.

지원하는 설정만 LLM에 제공하고 실행 직전에 기기 기능·프리셋 목록·서비스,
Assist 노출·사용자 제어 권한·가용 상태를 다시 검사합니다.
전원·풍속·상태 조회는 기본 Assist 도구를 사용합니다.
이 provider는 해당 도구를 추가하지 않으며 설정 명령 외의 전원·풍속 호출을 하지 않습니다.
기기 자체가 프리셋 등에 어떻게 반응하는지는 실제 장치에서 확인해야 합니다.
서비스 호출 완료는 명령 전달을 의미하며 물리적 동작 완료를 보장하지 않습니다.
실제 HA와 LLM에서 프리셋 전용·회전 전용·방향 지원 기기를 각각 검증하세요.

## 냉난방·팬 모드 제어

기존 LLM 대화 에이전트에서 **Assist Climate** Tool provider를 별도로 활성화합니다.
기존 통합 설치를 갱신한 뒤 HA를 재시작하면 선택할 수 있습니다.
현재 통합 초기 설정은 기존 위성 연결 절차를 사용하지만, climate 도구의 대상은
Assist에 노출한 `climate` 엔티티이며 요청 위성에 종속되지 않습니다.

- `AssistClimateSetHvacMode`: 자동(`auto`), 냉방(`cool`), 난방(`heat`), 제습(`dry`), 송풍(`fan_only`), 끄기(`off`), 냉난방 자동 전환(`heat_cool`)
- `AssistClimateSetFanMode`: 기기가 제공하는 팬 모드·풍속 값 설정
- `AssistClimateGetStatus`: 현재 모드와 지원 목록 조회

“거실 에어컨을 냉방으로 바꿔 줘”, “안방 에어컨 팬을 약풍으로 바꿔 줘”,
“보일러에서 어떤 모드를 쓸 수 있어?”처럼 요청합니다.
약풍 등 자연어 표현은 기기의 실제 지원 값으로 해석하며, 대응이 모호하면 질문합니다.

기기별 `hvac_modes`·`fan_modes`를 제공하고 실행 직전에 다시 검사합니다.
냉방 전용 기기에 난방을 요청하거나, 팬 기능이 없는 기기에 풍속을 요청하면 거절합니다.
미지원 모드로 임의 대체하지 않습니다. `auto`와 `heat_cool`은 별개의 모드이며,
자동 요청의 의미가 모호하면 확인하도록 LLM에 안내합니다.
송풍 운전 모드와 팬 풍속 설정도 구분합니다.

사용자 제어 권한, Assist 노출 여부, 기기 가용 상태와 서비스 지원을 확인합니다.
모드 변경은 즉시 실행하며, 예약·온도·프리셋·스윙 설정은 이번 기능에 포함하지 않습니다.
서비스 호출 완료는 명령 전달을 의미하며 물리적 동작 완료를 보장하지 않습니다.
실제 HA에서 냉방 전용·난방 전용 기기와 팬 모드 지원 기기의 음성 동작을 확인해야 합니다.

## 몇 분 후 기기 켜기·끄기

**Assist Scheduled Control** Tool provider를 별도로 활성화하면 예약 기기 제어를 사용할 수 있습니다.

- “5분 후 거실 조명 켜 줘.”
- “10분 후 선풍기 꺼 줘.”
- “거실 조명을 5분 후 켜고 10분 후 꺼 줘.”
- “기기 제어 예약 목록 보여 줘.”
- “방금 예약한 선풍기 끄기를 취소해 줘.”

LLM 도구는 `AssistScheduledControlCreate`, `AssistScheduledControlList`, `AssistScheduledControlCancel`입니다.
예약 시 대상 엔티티와 `turn_on` / `turn_off`를 확정하여 저장합니다.
시간이 되면 HA가 직접 실행하며, LLM에 명령을 다시 해석시키지 않습니다.
기기 이름이 모호하면 어느 기기인지 질문하도록 안내합니다.
예약 결과는 LLM이 답변하고 예약 실행 시 알람이나 `announce`를 재생하지 않습니다.

지원 대상은 Assist에 노출된 조명, 스위치, 선풍기, 냉난방기, 미디어 플레이어,
가습기, 온수기 중 해당 켜기·끄기 기능을 지원하는 엔티티입니다.
예약 생성과 실제 실행 시 노출 설정, 제어 권한, 가용 상태 및 기능 지원을 검사합니다.
계정이 있는 요청은 해당 사용자 ID로 실행하므로 예약 이후 권한이 바뀌면 실패 처리합니다.
기기 제어 예약에는 위성 선택이 필요하지 않습니다. 알람 타이머와 별도로 여러 예약을 저장합니다.

지연 시간은 1초부터 7일까지이며, 동시에 대기·실행 중인 예약은 최대 128개입니다.
예약마다 고유 `job_id`가 있고, 조회 시 최근 64개의 완료·실패·취소 기록도 확인할 수 있습니다.
취소는 대기 중인 예약만 가능하며, 이미 시작한 기기 동작을 되돌리지는 않습니다.
예약 ID로 취소하므로 “타이머 취소”와 “기기 제어 예약 취소”는 별도 명령입니다.

개발자 도구에서는 다음 작업으로 시험할 수 있습니다. 대상은 Assist에 노출되어 있어야 합니다.

```yaml
action: assist_timers.schedule_action
data:
  entity_id: light.living_room
  action: turn_on
  delay_seconds: 300
```

`assist_timers.list_actions`의 작업 응답에서 `job_id`를 확인하고 취소할 수 있습니다.

```yaml
action: assist_timers.cancel_action
data:
  job_id: 조회한_예약_ID
```

재시작 후 미래 예약은 복원하고, HA가 꺼진 사이 지난 예약은 `expired`로 처리합니다.
실행 도중 재시작된 예약은 `interrupted`로 남기고 자동 재실행하지 않습니다.
실행·권한·저장 실패는 상태와 `last_error`로 확인하며 자동으로 재시도하지 않습니다.
`completed`는 HA 서비스 호출이 정상 완료되었다는 뜻이며 물리적 기기의 응답은 실제 장치에서 확인해야 합니다.
마지막 통합 항목을 비활성화하면 예약 콜백을 중단하고, 재활성화 시 미래 예약을 복원합니다.
마지막 통합 항목을 삭제하면 저장된 기기 제어 예약도 삭제합니다.

## 수명 주기 및 복원

- 생성·일시정지·재개 상태를 HA `.storage`에 저장합니다.
- 실행 중 남은 시간은 단조 시계로 계산하고, 재시작 후에는 저장된 만료 시각을 사용합니다.
- 재시작 전에 일시정지된 타이머는 남은 시간을 그대로 유지합니다.
- HA가 꺼진 동안 이미 만료된 타이머는 재시작 시 제거하고 뒤늦게 알람을 재생하지 않습니다.
- 재시작 전 울리던 알람에는 정지 명령을 보내고 새로 재생하지 않습니다.
- 재생·정지 실패는 상태의 `last_error`로 확인할 수 있습니다. 정지 실패 시 슬롯을 유지하여 취소를 다시 시도할 수 있게 합니다.
- 설정에서 플레이어를 바꾸기 전에는 타이머를 취소해야 합니다. 위성 장치를 바꾸려면 항목을 삭제하고 다시 추가합니다.

## HA에서 검증하기

먼저 개발자 도구의 작업에서 짧은 타이머로 테스트합니다.

```yaml
action: assist_timers.create
data:
  satellite: assist_satellite.kitchen
  duration_seconds: 10
  name: 테스트
```

조회는 `assist_timers.status`, 일시정지는 `assist_timers.pause`, 재개는 `assist_timers.resume`,
취소는 `assist_timers.cancel` 또는 `assist_timers.stop`을 사용합니다.
각 작업의 `satellite`에는 설정한 위성 엔티티 ID를 넣습니다.
작업 응답을 요청하면 ID, 상태, 처음 설정한 시간과 남은 시간을 확인할 수 있습니다.
`timer_id`를 넣으면 현재 타이머와 일치하는지 검사하며, 생략하면 해당 위성의 단일 타이머를 제어합니다.

```yaml
action: assist_timers.cancel
data:
  satellite: assist_satellite.kitchen
```

다음 순서로 실제 장치에서 확인합니다.

1. 음성으로 10초를 설정한 후 LLM이 설정 결과를 답변하고 추가 `announce`가 없는지, 만료 시 알람이 일반 미디어로 재생되는지 확인합니다. 개발자 도구에서 직접 생성하면 확인 음성을 재생하지 않습니다.
2. 알람을 그대로 두면 약 1분 후 정지하는지 확인합니다.
3. 다시 설정하고 울리는 동안 “타이머 취소”를 말해 재생이 중단되는지 확인합니다. 확인 음성은 허용합니다.
4. 5분 타이머를 일시정지·재개하고, 원래 시간 문구와 실제 남은 시간이 분리되는지 확인합니다.
5. 같은 위성에서 두 번째 타이머 생성을 시도하고 중복 거절을 확인합니다.
6. 두 위성에서 동시에 설정하여 한쪽 취소가 다른 쪽 타이머에 영향을 주지 않는지 확인합니다.
7. 음성 디버그 추적에서 `AssistTimersCreate` / `AssistTimersCancel` 등의 도구가 호출되었는지 확인합니다.

## Windows 개발 검증

```powershell
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements-dev.txt
.venv/Scripts/python -m pytest -q
.venv/Scripts/ruff check custom_components tests tools
.venv/Scripts/ruff format --check custom_components tests tools
git diff --check
```

테스트는 타이머 핵심 로직과 HA 공개 API를 모사한 경계를 검사합니다.
실제 Home Assistant 실행, 위성의 음성 인식, 스피커 재생 완료를 대신하지 않습니다.
원본 알람을 바꾼 경우 `.venv/Scripts/python tools/prepare_alarm.py`로 60초 파일을 다시 생성합니다.

## 개발 및 배포 전 준비

1. `manifest.json`의 `YOUR_GITHUB_USERNAME`을 실제 저장소 소유자 또는 조직명으로 교체하고 저장소 주소를 확인합니다.
2. `codeowners`에 실제 유지관리자의 GitHub 사용자명(`@사용자명`)을 등록합니다.
3. 실제 Home Assistant와 위성에서 위 검증 절차를 완료합니다.
4. 배포 전 프로젝트 및 제공된 알람 사운드의 라이선스를 확인합니다.
5. HACS 검증을 수행하고 공식 문서의 브랜드 이미지 요건도 확인합니다.
6. 공개 GitHub 저장소의 설명과 주제를 설정하고, 버전별 배포가 필요하면 `0.1.0` GitHub Release를 발행합니다.

공개 저장소를 준비한 후 HACS 사용자 정의 저장소에 공개 저장소 URL을 **Integration** 유형으로 등록합니다.

## 개발 관리

- `tree.md`: 영문 파일 구조 및 역할
- `changelog.jsonl`: 영문 내부 개발 이력
- `caution.jsonl`: 영문 주의사항
- 소스와 문서는 UTF-8(BOM 없음)으로 유지합니다.
- Git은 로컬 `dev` 브랜치부터 관리합니다. 원격 저장소 연결 전에는 push할 수 없습니다.
- 로그 기능을 구현할 때 기본 레벨은 INFO로 하고, 인증 정보를 기록하지 않습니다.
- 이 통합의 로그는 HA의 기본 로그 레벨을 따릅니다. 정상 동작은 INFO, 복원 주의사항은 WARN, 출력 실패는 ERROR로 기록하며 글로벌 로그 설정을 바꾸지 않습니다.
- 원본은 `backup/scaffold-before-implementation.zip`과 로컬 `dev` 백업 커밋에 보존했습니다. 사용자 요청에 따라 원격 업로드는 테스트 이후로 보류합니다.

## 공식 개발 문서

- [HACS 통합 요구사항](https://www.hacs.dev/docs/publish/integration/)
- [HACS 공통 요구사항 및 hacs.json](https://hacs.dev/docs/publish/start/)
- [Home Assistant 통합 파일 구조](https://developers.home-assistant.io/docs/creating_integration_file_structure/)
- [Home Assistant manifest 규격](https://developers.home-assistant.io/docs/creating_integration_manifest/)
- [LLM API 및 도구 등록](https://developers.home-assistant.io/docs/core/llm/)
- [Assist 위성의 announce 기능](https://developers.home-assistant.io/docs/core/entity/assist-satellite/)
- [로컬 명령 우선 처리 설명](https://www.home-assistant.io/blog/2025/02/13/voice-chapter-9-speech-to-phrase/)

문서 확인일: 2026-10-14.
