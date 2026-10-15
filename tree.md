# Project tree

```text
assist_timers/
|-- .gitattributes                  # Text and line-ending rules
|-- .gitignore                      # Local artifacts and secrets exclusions
|-- README.md                       # Korean project overview
|-- tree.md                         # English structure reference
|-- changelog.jsonl                 # Internal development history
|-- caution.jsonl                   # Development cautions
|-- hacs.json                       # HACS metadata and minimum HA version
|-- pyproject.toml                  # Local pytest and Ruff configuration
|-- requirements-dev.txt            # Windows validation and audio build tooling
|-- tests/
|   |-- conftest.py                 # HA public-interface contract doubles
|   |-- test_timer_manager.py       # Lifecycle, real scheduling, restoration, races
|   |-- test_scheduled_actions.py   # Delayed device jobs, permissions, restoration
|   |-- test_climate_tools.py       # Device-specific HVAC/fan modes and authorization
|   |-- test_fan_tools.py           # Fan presets, oscillation, direction and permissions
|   `-- test_tools_and_output.py    # Routing, API, media, setup, and configuration
|-- tools/
|   `-- prepare_alarm.py            # Build the bounded alarm from the source MP3
`-- custom_components/
    `-- assist_timers/
        |-- __init__.py             # Setup, shutdown, storage, and HA lifecycle actions
        |-- config_flow.py          # Unique satellite and media-player bindings
        |-- const.py                # Shared domain, duration limits, and media URL
        |-- llm.py                  # Selectable API, alarm tools, and device job tools
        |-- action_tools.py         # LLM and HA action validation for scheduled jobs
        |-- climate_tools.py        # Independent climate provider, capabilities and calls
        |-- fan_tools.py            # Independent fan settings provider and validation
        |-- device_control.py       # Assist exposure, permissions, power capabilities
        |-- manifest.json           # HA metadata, config flow, version 0.1.0
        |-- runtime.py              # Device routing, deduplication, confirmations
        |-- satellite.py            # Announce speech and normal-media play/stop
        |-- scheduled_actions.py    # Durable delayed device on/off jobs
        |-- timer_manager.py        # One persisted timer per satellite
        |-- services.yaml           # Lifecycle action descriptions
        |-- strings.json            # Source UI strings
        |-- translations/
        |   |-- en.json             # English UI strings
        |   `-- ko.json             # Korean UI strings
        `-- alarm_sounds/
            |-- beep-alarm-366507.mp3 # User-provided original audio
            `-- alarm_60s.mp3         # Generated bounded 60-second alarm
```

Only one integration is included in custom_components. No separate conversation
agent is installed; the existing LLM agent independently selects the Assist Timer
and Assist Scheduled Control, Assist Climate and Assist Fan APIs. The project display name is Assist Tools; the existing
assist_timers directory and domain remain stable for compatibility.
The .git directory, .venv, test/lint caches, backup archives, and dist artifacts
are excluded from this source tree. Tests use HA boundary doubles; real HA and
satellite testing remain a separate installation step.
