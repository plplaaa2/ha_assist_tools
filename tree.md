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
|   `-- test_tools_and_output.py    # Routing, API, media, setup, and configuration
|-- tools/
|   `-- prepare_alarm.py            # Build the bounded alarm from the source MP3
`-- custom_components/
    `-- assist_timers/
        |-- __init__.py             # Setup, shutdown, storage, and HA lifecycle actions
        |-- config_flow.py          # Unique satellite and media-player bindings
        |-- const.py                # Shared domain, duration limits, and media URL
        |-- llm.py                  # Selectable Assist Timers API and five tools
        |-- manifest.json           # HA metadata, config flow, version 0.1.0
        |-- runtime.py              # Device routing, deduplication, confirmations
        |-- satellite.py            # Announce speech and normal-media play/stop
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
agent is installed; the existing LLM agent selects the Assist Timers API.
The .git directory, .venv, test/lint caches, backup archives, and dist artifacts
are excluded from this source tree. Tests use HA boundary doubles; real HA and
satellite testing remain a separate installation step.
