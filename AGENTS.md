# Repository Guidelines

## Project Structure

- `workbuddy-session-sync.py` is the CLI synchronizer. It reads and updates the local WorkBuddy SQLite database, conversation JSONL files, and task directories.
- `workbuddy-sync-app.py` provides the local web UI and its account/session synchronization flow.
- `macos/WorkBuddySyncApp.swift` is the native WebKit shell; `build-macos-app.sh` assembles the signed WorkBuddy Session Harbor desktop `.app` and icon.
- `overview.md` records the verified storage model, synchronization options, and operational risks. Update it when assumptions about WorkBuddy storage change.

This repository has no package manifest or test directory. The synchronizer uses the Python standard library; the web UI also uses the already-installed `cryptography` package solely to read Cockpit's encrypted local account store. Do not add dependencies without a concrete compatibility need.

## Development and Verification

Run commands from the repository root:

```bash
python3 workbuddy-session-sync.py --dry-run   # report pending DB changes without writing
python3 workbuddy-session-sync.py --whoami    # inspect current WorkBuddy account
python3 workbuddy-sync-app.py --port 7531     # start the local UI
zsh build-macos-app.sh                        # build the Desktop macOS app
python3 -m py_compile workbuddy-session-sync.py workbuddy-sync-app.py
python3 workbuddy-sync-app.test.py
```

Before any write-path test, back up `~/.workbuddy/workbuddy.db` and close WorkBuddy when practical to avoid WAL lock contention. Prefer `--dry-run` first; use real accounts and data only when the requested change requires it.

## Coding Style

Use Python 3 with four-space indentation, `snake_case` functions and variables, uppercase module constants, and concise Chinese user-facing messages to match existing scripts. Keep dependencies at zero unless the platform already supplies one. Use parameterized SQLite queries; never interpolate account IDs or user input into SQL. Keep Node code compatible with the installed CommonJS runtime and use `camelCase` names.

## Testing Guidelines

There is no broad automated suite; the focused authentication-session check is:

```bash
python3 workbuddy-sync-app.test.py
```

For logic changes, add the smallest focused check that proves the changed behavior, then run the syntax checks above. For database changes, verify both the reported preflight count and a post-write readback; do not treat a successful command exit as sufficient verification.

For desktop-shell changes, rebuild on macOS, verify `codesign --verify --deep --strict`, then cold-launch the app after stopping the standalone service.

## Commits and Pull Requests

Recent commits use concise Conventional Commit-style Chinese messages, for example `fix: 原子 kill+写入` and `fix: 显式 await db.open() 再写入`. Keep each commit scoped to one operational fix. Pull requests should describe the affected data path, commands run, verification results, and rollback/backup considerations. Never commit WorkBuddy databases, exports, tokens, or other account data.
