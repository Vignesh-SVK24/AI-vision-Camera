# Final Release Checklist, Test Report & Known Limitations

## 1. Automated Verification Summary (Observed in Current Environment)

### Verified Passing in This Session
Run with system Python `3.11.4` (`python -m compileall` + standalone `unittest` modules that rely only on standard library + installed `fastapi`/`starlette`/`httpx`/`numpy`/`pillow`):

| Check / Test File | Scope Covered | Observed Result |
|---|---|---|
| `python -m compileall -q` (all Python dirs + `main.py`) | Syntax & bytecode compilation across 12 folders | **Passed** (exit `0`) |
| `tests/test_go2rtc_config.py` | Localhost API bind, RTSP output disabled, credential URL-encoding | **3 / 3 passed** (`OK`) |
| `tests/test_security_hardening.py` | Loopback/Tailscale bind check, ASGI security headers, `413` body limits, `429` tiered rate limits, `.gitignore` & secret scan | **3 / 3 passed** (`OK`) |
| `tests/test_cloud_sync.py` | `CLOUD_SYNC=false` zero-egress, privacy-filtered Supabase payload, thumbnail resize/path guard, backoff/confirm, 30-day Storage+DB cleanup | **4 / 4 passed** (`OK`) |
| `tests/test_reliability_and_soak.py` | Simulated internet loss/recovery, camera reconnect, frame-stall watchdog restart, PC restart WAL persistence, disk guard cutoff, hot SQLite backup, soak CSV | **5 / 5 passed** (`OK`) |
| `tools/secret_scan.py` | Secret patterns across code/configs/docs + `.gitignore` rules | **Passed** (exit `0`, zero findings) |
| `tools/harden_firewall.ps1 -Status` | Active listener audit (`8000, 5173, 1984, 8555, 554`) & firewall rule state | **Passed** (exit `0`, no active listeners) |
| `tools/service_runner.ps1 -Status -ProjectRoot <repo>` | Windows Scheduled Task check (`VisionGuard-*`) | **Passed** (`exit 0`, all 4 tasks `[NOT INSTALLED]`) |
| `tools/service_runner.ps1 -Status` (without `-ProjectRoot`) | Default `$ProjectRoot` parameter evaluation | **Failed** (`exit 1` in PowerShell 5.1: `Split-Path` gets empty `$PSScriptRoot`) |
| `python -m pytest tests -v` | Full pytest collection & remaining 8 suites | **Failed / Not run** (`exit 1`: `No module named pytest`) |

### Test Suites Requiring Dependency Installation (`pip install -r requirements.txt` & `npm install`)
Per workspace rules (*ask before installing any new dependency*), these packages were **not** auto-installed (`pytest`, `python-dotenv`, `opencv-python`, `ultralytics`, `argon2-cffi`, `PyJWT`, frontend `node_modules`). Run them immediately after approving installation:

- `tests/test_smoke.py` — config loading, password masking, `main.py` banner
- `tests/test_rtsp_client.py` — RTSP TCP forcing, latest-frame buffer, exponential backoff, watchdog
- `tests/test_person_detector.py` — MOG2 motion gate, YOLO person-only filter, OpenVINO export/fallback, FPS limiter
- `tests/test_zones_and_tracking.py` — ByteTrack tracking, polygon entry/exit/dwell rules
- `tests/test_events_and_storage.py` — SQLite WAL migrations, cooldowns, snapshot resizing, retention purge
- `tests/test_faces.py` — YuNet/SFace enrollment validation, cosine matching, consent gating, deletion
- `tests/test_telegram.py` — Telegram multipart upload, alert priority rules, offline outbox retry, rate limit
- `tests/test_backend_api.py` — FastAPI `/auth/*`, `/events`, `/snapshots/{id}`, `/people`, `/zones`, `/settings`, `/ws/events`
- `dashboard` (`npm run build` / TypeScript `tsc -b` + Vite bundle)

---

## 2. Pre-Release Acceptance Checklist

### Security & Privacy
- [x] `.env`, `.env.*`, `*.db`, `snapshots/`, `faces_data/`, `models/`, `logs/`, `venv/` ignored in `.gitignore`.
- [x] `tools/secret_scan.py` passes with zero hardcoded credentials in project files.
- [x] `backend/app.py` enforces loopback or Tailscale-only bind (`validate_bind_host`), ASGI security headers, strict CORS, request size limits, and tiered `429` rate limits.
- [x] `config/go2rtc.yaml` binds API strictly to `127.0.0.1:1984` and disables RTSP output (`rtsp.listen: ""`).
- [x] Face recognition runs locally (`YuNet` + `SFace`); `CLOUD_SYNC` defaults to `false` and never exports face crops, embeddings, consent notes, or full names.
- [ ] Generate unique `JWT_SECRET_KEY` in `.env` and create owner login via `python tools\create_owner.py --username admin`.
- [ ] Apply Windows Firewall lockdown (`.\tools\harden_firewall.ps1 -Apply`) and verify zero open LAN ports (`Test-NetConnection` / `nmap`).
- [ ] Install `pip-audit` and run `pip-audit -r requirements.txt` + `npm audit` after dependency installation (see `docs/DEPENDENCY_AUDIT.md`).

### Functional & UI Verification
- [ ] `npm run build` succeeds with zero TypeScript errors and all React routes (`/overview`, `/live`, `/events`, `/people`, `/zones`, `/notifications`, `/settings`, `/design`) render cleanly at `1440px` desktop and `390px` mobile.
- [ ] End-to-end camera check passes (`python tools\camera_check.py`) and benchmark meets CPU budget (`python tools\benchmark.py`).
- [ ] `python tools\download_models.py` verified locally.

### Reliability & Operations
- [ ] Windows power plan configured never to sleep on AC (`docs/WINDOWS_RELIABILITY.md`).
- [ ] Startup services and weekly external-drive SQLite backup registered (`.\tools\service_runner.ps1 -Install -ProjectRoot (Get-Location).Path`).
- [ ] 24-hour soak test (`python tools\soak_test.py --duration-seconds 86400`) confirms bounded RSS memory growth.

---

## 3. Known Limitations

1. **Dependencies not installed in this workspace:** Neither Python packages (`requirements.txt`, `pip-audit`) nor frontend npm packages (`dashboard/package.json`) were installed here without explicit user confirmation, so `pytest`, `pip-audit`, `npm run build`, and `npm audit` remain to be executed after approval.
2. **Phase 11 live streaming / PWA tasks remain incomplete:** After strict review of the go2rtc setup phase, `go2rtc` configuration (`config/go2rtc.yaml`, `tools/run_go2rtc.py`, and security-reviewed `dashboard/vite.config.ts`) was put in place, while authenticated FastAPI WebRTC signaling endpoints, the `LivePage` WebRTC + fallback player, and PWA manifest/icons/service worker were paused pending confirmation.
3. **Engine runner live hardware verification:** End-to-end execution of `tools/run_engine.py` against a physical RTSP camera, hardware OpenVINO export on target Intel integrated graphics, live Telegram delivery, live Supabase RLS tables, and Windows reboot task triggering require testing on the physical host PC.
4. **In-memory rate limiters:** Backend `LoginRateLimiter` and `ConsistentRateLimitMiddleware` maintain sliding windows in single-process memory; running multiple Uvicorn workers (`--workers > 1`) is not recommended on this local single-host setup.
5. **No built-in UI database backup/restore button:** Weekly external-drive SQLite backups run via CLI / Windows Task Scheduler (`tools/backup_db.py` and `tools/service_runner.ps1`).
6. **PowerShell 5.1 requires `-ProjectRoot` on `tools/service_runner.ps1`:** Default `[string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot)` evaluates before `$PSScriptRoot` is populated in Windows PowerShell 5.1 (`exit 1`); pass `-ProjectRoot (Get-Location).Path` explicitly when invoking `service_runner.ps1`.
