# llama-launcher - agent notes

Test-case IDs used in test names and comments (A1, P7, U3...) are in `docs/test-cases.md`.

## Environment

- Python 3.13, venv in `.venv`.
  Run everything with `.venv\Scripts\python.exe`, never a bare `python`.
- Depend on `PySide6-Essentials`, not the `PySide6` meta package (it pulls WebEngine; ~1 GB).
- Tests: `.venv\Scripts\python.exe -m pytest`.
  Real-binary tests are opt-in (`-m real`)
  and read `LLAMA_LAUNCHER_TEST_MODELS` / `LLAMA_LAUNCHER_TEST_RUNTIMES`
  from the environment or the git-ignored `.env` (see `.env.example`);
  never hard-code machine paths.
- Lint and format: `.venv\Scripts\python.exe -m ruff check .` and `.venv\Scripts\python.exe -m ruff format .`
  (config in `pyproject.toml`);
  run both before committing.
- Build: `.venv\Scripts\python.exe tools\build.py` -> `dist\llama-launcher\`.
- README logo and screenshots: `.venv\Scripts\python.exe tools\screenshots.py` -> `docs\images\`
  (placeholder models; aborts if a local path is visible).
- Imports are absolute (`from llama_launcher.x import y`).

## Releasing (only when asked)

Bump `pyproject.toml` version, push `main`, then push tag `v<version>`:
`publish.yml` publishes to PyPI and attaches the Windows zip.
Add notes with `gh release edit`.

## Layout

| module | role |
|---|---|
| `gguf.py` | header-only GGUF reader |
| `catalog.py` | folder scan, file kinds, sidecar suggestions, info cache |
| `runtimes.py` | runtime folders, `--version` / `--help` parsing into a flag table |
| `server_flags.py` | every llama-server flag the app sets or reads, and which config field maps to which |
| `args.py` | tokenizer (backslashes literal) and the later-wins flag merger |
| `config.py` | settings/profiles TOML via tomlkit, comment-preserving saves |
| `launch.py` | profile -> argv/env + pre-launch validation (pure) |
| `server.py` | one llama-server process: start, readiness, sleep/idle, stop, port watch |
| `errors.py` | failure classification from exit code + server.log tail |
| `cmdimport.py` | profiles from llama-server command lines (dry run by default) |
| `controller.py` | Qt glue; `ui/` holds tray, settings window, profile editor |
| `ui/tabs/` | one module per settings tab; `SettingsTab` edits the window's working copies |

## Rules

- Only ever stop the PID this app spawned (`ServerManager._proc`).
  Never kill by name, never adopt or stop a server someone else started: it shows as `EXTERNAL`.
- Children are in a kill-on-close Job Object, so they die with the app.
- Check the port before spawning:
  on Windows llama-server binds a port another server already listens on (address reuse),
  so a bind failure can't be relied on.
- `launch.build` must stay pure;
  anything that touches disk is passed in (`get_info`, runtime).
- Tests spawn the fake server with `sys._base_executable`:
  the venv `python.exe` is a redirector whose real interpreter is a grandchild.
- Fixture logs were captured from real runs; keep machine-specific paths out of them.

## llama-server facts

- No flag is repeatable; lists are comma-separated, so later-wins is always right.
- `/props` has `is_sleeping`;
  `/health` needs no API key, `/props` and `/v1/models` do.
- `LLAMA_API_KEY` env works; the key never goes on the command line.
- `--host` takes a comma-separated list (e.g. `127.0.0.1,<tailscale ip>`).
- Out-of-VRAM rarely fails on Windows:
  WDDM spills to shared memory and generation slows instead.
- Crashes: wrong draft model 0xC0000005;
  an MTP draft without `--spec-type draft-mtp` 0xC0000409.
  Wrong mmproj exits 1 with an n_embd mismatch line.
