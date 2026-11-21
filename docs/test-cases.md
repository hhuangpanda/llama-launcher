# Test cases

IDs used in test names and comments.

`tests/unit` and `tests/ui` run on every change (`pytest`). `tests/integration` uses
`tests/fixtures/fake_server.py`: a small HTTP server that accepts llama-server style args,
answers `/health`, `/v1/models`, `/slots`, `/props`, prints the real `--help`/`--version` captured in
Phase 0, and can be told to delay readiness, exit with a code and message, or go idle/sleep.
Runtimes accept a command prefix so tests run `[python, fake_server.py]` in place of the exe.
GGUF fixtures are tiny header-only files written by a test helper. `tests/real` (marker `real`, opt-in) uses real binaries and models from the env vars
`LLAMA_LAUNCHER_TEST_MODELS` and `LLAMA_LAUNCHER_TEST_RUNTIMES` (environment or the git-ignored `.env`).

### GGUF + catalog (unit)

| id | case | expect |
|---|---|---|
| G1 | read header of synthetic model/mmproj/draft files | arch, type, embedding_length, projection_dim, nextn layers parsed |
| G2 | truncated / non-GGUF / v2 file | clear error, no crash; file listed as "unreadable" |
| G3 | metadata array and string values of all types | parser skips them correctly to reach later keys |
| C1 | flat folder with several families, mmprojs and a draft | each model gets the mmproj whose projection dim matches, the draft whose arch matches |
| C2 | two mmprojs with the same projection dim | no auto-attach; both offered |
| C3 | model with no matching mmproj | mmproj empty, no warning |
| C4 | draft whose arch doesn't match | not suggested; picked manually -> validation error |
| C5 | split model `-00001-of-00003` + shards | one model entry, shards hidden |
| C6 | nested folders, junction loop, unreadable dir | scan completes, loop not followed |
| C7 | same file under two enrolled folders | listed once |
| C8 | hard links into per-model subfolders | each link listed as its own path, pairs within its folder |

### Config (unit)

| id | case | expect |
|---|---|---|
| F1 | load, edit one field, save | other content and user comments preserved byte-for-byte |
| F2 | two profiles on one file, ctx set via UI path, then via quick picker "save as default" | exactly one `ctx_size` per profile; no duplicate model entries (regression for bug 1) |
| F3 | invalid TOML on disk | last good config kept, error surfaced |
| F4 | external edit while running | reload fires once (debounced), restart badge if argv changed |
| F5 | profile references missing file | profile marked missing, others unaffected |
| F6 | API key set/cleared in Settings | `api_key` written to settings.toml; empty string = no key |

### Args (unit)

| id | case | expect |
|---|---|---|
| A1 | global `--ctx-size 102400`, profile ctx 76800 | single `--ctx-size 76800` |
| A2 | global `-c 4096`, profile `--ctx-size 8192` | alias resolved, one flag, 8192 |
| A3 | global `--kv-unified`, profile `--no-kv-unified` | only `--no-kv-unified` |
| A4 | profile `unset = ["--spec-type"]` with global `--spec-type draft-mtp --spec-draft-n-max 2` | `--spec-type` gone; `--spec-draft-n-max` kept unless also unset |
| A5 | repeatable `-ot` in global and profile | both present, global first |
| A6 | `--port` / `--host` / `--api-key` / `-m` in raw args | config error naming the flag |
| A7 | quoted value with spaces and JSON (`--chat-template-kwargs '{"preserve_thinking":true}'`) | passed as one argv item, unchanged |
| A8 | unbalanced quote | config error with position |
| A9 | flag unknown to the selected runtime | validation error naming flag and runtime |
| A10 | same profile on two runtimes with different help | canonicalization uses each runtime's table |
| A11 | idle sleep 3600 | `--sleep-idle-seconds 3600`; idle stop/off -> flag absent |
| A12 | one-shot ctx 32k | applies to that start only; config file unchanged |
| A13 | mmproj/draft fields | `--mmproj`/`-md` rendered once; raw `--mmproj` in args wins and warns |
| A14 | profile imported from a command line | rebuilds the same flags as the command (minus host/port/order) |

### Runtimes (unit + real)

| id | case | expect |
|---|---|---|
| R1 | folder without `llama-server.exe` | rejected with message |
| R2 | `--version` output fixtures (release, local build) | build number + commit parsed and shown |
| R3 | help cache | re-parsed only when exe mtime changes |
| R4 (real) | both real runtimes | version parsed, help table has ctx-size, spec-type, sleep-idle-seconds |

### Process manager + idle (integration, fake server)

| id | case | expect |
|---|---|---|
| P1 | start -> ready -> stop | states in order; process gone; server-cwd emptied |
| P2 | start B while A runs | A stopped (exit confirmed) before B spawns |
| P3 | server exits 1 during load with "unknown argument" | `failed`, message names the flag, log path given |
| P4 | readiness timeout | child killed, `failed: timeout` |
| P5 | port held by a foreign process | `external`; foreign PID untouched after start, stop, quit |
| P6 | app process killed hard | child gone within 2 s (job object) |
| P7 | edit profile args while running | restart badge; restart runs new argv (regression for bug 4) |
| P8 | edit settings while stopped, then start | new argv used, no app restart needed |
| P9 | stop mode, no activity for N s (N=2 in test) | stopped, reason "idle" in log |
| P10 | stop mode, activity every N/2 s | stays running |
| P11 | sleep mode, fake reports sleeping | tray state sleeping; request -> awake |
| P12 | quit while starting | spawn aborted, nothing orphaned |
| P13 | API key enabled | child env has `LLAMA_API_KEY`; argv and log don't contain it |
| P14 | listen 0.0.0.0 | `--host 0.0.0.0`; Open web UI still uses 127.0.0.1 |

### Command-line import (unit)

| id | case | expect |
|---|---|---|
| I1 | new commands | runtimes/folders added by path, shared flags become global args, sidecars become model fields, every profile rebuilds its command |
| I2 | same input again | all unchanged, nothing written |
| I3 | changed command | conflict, skipped; `--replace` overwrites |
| I4 | existing runtime / parent folder | matched by path, not added again |
| I5 | model already has another mmproj | command's mmproj kept in profile args |
| I6 | global flag absent from a command | profile unsets it |
| I7 | no `-m`, same model + name twice, bad JSON | reported per entry |
| I8 | CLI | dry run by default; `--write` applies; re-run writes nothing |

### Errors + logs (unit)

| id | case | expect |
|---|---|---|
| E1 | each Phase 0 log fixture | correct class and one-line message |
| E2 | unknown output | "exited with code X" + last 5 lines |
| L1 | server run | `server.log` has header with profile, runtime, version, argv; output follows live |
| L2 | run fails, then app sits idle | `server.log` still holds the failed run with exit code |
| L3 | second start after a failed run | failed run is in `server.prev.log`, new run in `server.log` |
| L4 | third start | oldest run gone; only `server.log` and `server.prev.log` exist |
| L5 | app relaunch | `app.log` moved to `app.prev.log`, new `app.log` started |
| L6 | `server.prev.log` locked (open in an editor) | start still proceeds, rotation skipped and noted in `app.log` |

### UI (pytest-qt)

| id | case | expect |
|---|---|---|
| U1 | open Settings twice (tray and second app launch) | one window, raised and focused (regression for bug 3) |
| U2 | Settings window flags | top-level, not a tool window, so it has a taskbar button |
| U3 | tray menu from fixture config | models/profiles as specified; single-profile model is a direct item |
| U4 | state changes | icon and tooltip follow P-states; Stop enabled only when running |
| U5 | profile editor change | command preview and validation update without saving |
| U6 | mmproj dropdown | compatible files first, mismatch shows warning |
| U7 | context picker "Save as default" | writes `ctx_size` once to that profile |
| U8 | Copy model id / curl | clipboard holds alias / valid curl with port (and key header if enabled) |
| U9 | launch at startup toggle | Run key created/removed with the right command line |
| U10 | second app instance | exits immediately, first instance opens Settings |

### Real (opt-in, `pytest -m real`)

| id | case | expect |
|---|---|---|
| X1 | Gemma QAT profile at 4096 ctx | ready, `/v1/models` shows alias, one chat completion succeeds, stop frees the process |
| X2 | Qwen3.8 GSQ from flat a flat model folder with suggested sidecars | loads (the case the Llama app failed) |
| X3 | profile with a deliberately wrong mmproj | blocked by validation before launch |
| X4 | sleep mode with idle 30 s | sleeps, then wakes on a request |

### Requirement coverage

| requirement | tests |
|---|---|
| idle shutdown | P9-P11, X4 |
| custom model folders, port | C1-C8, P14 |
| KV cache options | A1, A11, U5 |
| global custom args | A1-A9 |
| per-model arg overrides | A1, A4, A13, U5 |
| multiple launch options per GGUF | F2, U3, A14 |
| import existing launch commands | I1-I8 |
| tray start/stop + context sizes | U3, U4, U7, A12, P1, P2 |
| llama.cpp version shown, custom builds | R1-R4, A10 |
| launch at startup | U9 |
| logs in one place, last two runs kept | L1-L6, E1 |
| no dup context entries (bug 1) | F2 |
| mixed sidecars in one folder (bug 2) | C1, C2, X2, X3 |
| settings window (bug 3) | U1, U2, U10 |
| flags picked up on restart (bug 4) | P7, P8 |
