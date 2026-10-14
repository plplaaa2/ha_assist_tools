# Project tree

```text
assist_timers/
|-- .gitattributes                  # Text and line-ending rules
|-- .gitignore                      # Local artifacts and secrets exclusions
|-- README.md                       # Korean project overview
|-- tree.md                         # English structure reference
|-- changelog.jsonl                 # Internal development history
|-- caution.jsonl                   # Development cautions
|-- hacs.json                       # HACS repository metadata
`-- custom_components/
    `-- assist_timers/
        |-- __init__.py             # Integration package scaffold
        |-- const.py                # Integration domain constant
        `-- manifest.json           # Home Assistant metadata, version 0.1.0
```

Only one integration is included in custom_components. Runtime behavior and
configuration flows are intentionally pending requirements and plan approval.
Platform modules, services, translations, and tests should be added when their
corresponding behavior is defined. The .git directory is excluded from this tree.
