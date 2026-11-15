# Vision Surveillance — Project Cleanup and Restructure Audit Report

**Date**: 2026-11-15  
**Branch**: `cleanup-restructure`  
**Baseline Status**: 53 passed in 154.51s (100% tests passing) | Frontend build: 0 errors | Live Camera: Functional  
**Auditor**: Senior Full-Stack Engineer & Product Designer  

---

## 1. Executive Summary & Verification Baseline

Before performing any deletions or architectural modifications, a complete safety baseline was established on the newly created `cleanup-restructure` branch.

### Baseline Verification Results
| Verification Item | Command | Result | Notes |
| :--- | :--- | :--- | :--- |
| **Python Test Suite** | `python -m pytest -v` | **53 passed, 0 failed** (154.5s) | Complete test coverage across auth, detector, tracking, faces, zones, api, storage. |
| **Frontend Production Build** | `npm --prefix dashboard run build` | **Build Success (0 errors)** | `dist/assets/index.js` (290.12 kB), `dist/assets/index.css` (20.49 kB). |
| **Camera Hardware** | `python tools/list_cameras.py` | **Index 0 verified** | Operational at 640x480 with both DSHOW and MSMF backends. |
| **Cloud Connectivity** | Cloudflare Tunnel `task-1776` | **HTTP 200 OK** | Tunnel active on port 8000 serving `/health` and `/api/health`. |

---

## 2. Codebase Audit Findings

### 2.1 Unused Files (Proven Dead Code — Scheduled for Deletion)
| File Path | LOC | Reason for Deletion |
| :--- | :--- | :--- |
| `dashboard/src/pages/DesignDemoPage.tsx` | 338 | Demo showcase page unreferenced in core user flows; contains mock data and placeholder elements. |
| `dashboard/src/pages/DesignDemoPage.css` | 104 | Styling specific only to `DesignDemoPage`. |
| `tools/diagnose_cameras.py` | 82 | Temporary single-purpose diagnostic script. Capabilities are fully integrated into `tools/list_cameras.py`. |
| `camera/rtsp_client.py` | 622 | Monolithic legacy camera client. To be fully replaced by modular Part A architecture (`base.py`, `webcam.py`, `url_stream.py`, `file_source.py`, `factory.py`, `health.py`). |

### 2.2 Frontend Pages Scheduled for Architectural Consolidation
| Current File | Target Destination | Rationale |
| :--- | :--- | :--- |
| `dashboard/src/pages/ZonesPage.tsx` (380 LOC) | Merged into `SettingsPage.tsx` | Part C requirement: zones configuration belongs directly in Settings to maintain a strict 4-destination navigation architecture. |
| `dashboard/src/pages/NotificationsPage.tsx` (124 LOC) | Merged into `EventsPage.tsx` | Part C requirement: alert and notification history belongs natively within the Events feed. |
| `dashboard/src/pages/OverviewPage.tsx` (256 LOC) & `LivePage.tsx` (334 LOC) | Consolidated into unified `HomePage.tsx` | Part C requirement: One large live-view hero glass card, compact "now" strip, and vertical event timeline. |

### 2.3 Unused Functions (Dead Code)
| File Path | Function / Identifier | Reason / Action |
| :--- | :--- | :--- |
| `faces/matcher.py:32` | `cosine_similarity()` | Helper function defined but never called; `FaceMatcher` computes similarity via vectorized `np.dot(query, entry.vector)` directly. **Action: Delete.** |

### 2.4 Unused Imports (Verified via AST Parser)
| File Path | Unused Import | Action |
| :--- | :--- | :--- |
| `backend/auth.py:8` | `Optional` from `typing` | Remove |
| `events/event_manager.py:8` | `timezone` from `datetime` | Remove |
| `faces/enrollment.py:11` | `Iterable` from `typing` | Remove |
| `faces/matcher.py:13` | `DetectedFace` from `faces.face_engine` | Remove |
| `notifications/telegram.py:9-10` | `urllib.error`, `urllib.request` | Remove |
| `storage/cloud_sync.py:9-11` | `urllib.error`, `urllib.parse`, `urllib.request` | Remove |
| `tests/test_faces.py:7` | `cv2` | Remove |
| `tools/benchmark.py:12` | `Any` from `typing` | Remove |
| `tools/download_models.py:8` | `urllib.request` | Remove |
| `tracking/zones.py:8` | `field` from `dataclasses` | Remove |

### 2.5 Dependencies Audit
- **Python Dependencies (`requirements.txt`)**:
  - Declared: `opencv-python`, `ultralytics`, `numpy`, `python-dotenv`, `pytest`, `fastapi`, `uvicorn`, `python-multipart`, `httpx`, `argon2-cffi`, `PyJWT`.
  - **Verdict**: **All 11 packages are actively imported and required**. No bloat or unused top-level dependencies.
- **Frontend Dependencies (`dashboard/package.json`)**:
  - Runtime: `react` (^19.0.0), `react-dom` (^19.0.0).
  - Dev: `@types/react`, `@types/react-dom`, `@vitejs/plugin-react`, `typescript`, `vite`.
  - **Verdict**: **Zero unused npm packages**. Extremely lightweight footprint (~290 kB bundled).

### 2.6 Debug Prints & Logging Audit
- **Core Backend (`backend/`, `camera/`, `detection/`, `faces/`, `storage/`, `tracking/`)**:
  - **Zero unformatted `print()` statements**. All modules cleanly use `logging.getLogger(__name__)`.
- **CLI Runner (`main.py`)**:
  - Contains 10 console user-feedback `print()` statements. These will be updated to clean structured logging while retaining clear user instructions when camera initialization fails.

### 2.7 UI Copy & Filler Text Audit
- `DesignDemoPage`: Contains dummy placeholder text and test inputs (`"Filter by person or zone..."`, `"Enter at least 8 chars"`). Removing this page eliminates all mock copy.
- Form inputs across `LoginPage`, `PeoplePage`, and `EventsPage`: Reviewed to ensure concise, human, and self-explanatory label copy without filler or jargon.

---

## 3. Items Listed for User Decision (Uncertain Items)

The following utility scripts in `tools/` are not imported by the main application runtime, but provide developer operations:
1. `tools/harden_firewall.ps1` & `tools/service_runner.ps1` (Windows PowerShell administration scripts).
2. `tools/secret_scan.py` & `tools/soak_test.py` (Local testing & security compliance tooling).
3. `tools/camera_calibrate.py` & `tools/benchmark.py` (Performance profiling and camera calibration).

> **Recommendation**: **Retain these developer tools**. They do not add runtime overhead and are valuable for deployment, performance testing, and system maintenance.

---

## 4. Execution Plan (Step-by-Step)

### Phase A: Camera & Connectivity Rewrite
1. Create `camera/base.py`:
   - Abstract `CameraSource` ABC defining `start()`, `stop()`, `get_latest_frame()`, `status`, `on_status_change()`, `fps`, `is_connected`, and `masked_source`.
2. Implement specific sources:
   - `camera/webcam.py`: `WebcamSource` with `WEBCAM_INDEX`, `WEBCAM_BACKEND` (`dshow` / `msmf`), warmup handling, and descriptive Windows error diagnostics.
   - `camera/url_stream.py`: `URLStreamSource` for HTTP/RTSP streams with TCP transport options and credential masking.
   - `camera/file_source.py`: `FileSource` for video clips / testing with loop support.
3. Implement `camera/health.py`:
   - Watchdog thread, stale-frame detection, exponential backoff with jitter on reconnect, emitting `camera_online` and `camera_offline` events.
4. Implement `camera/factory.py`:
   - Single entry point `create_camera_source(settings)` mapping `CAMERA_SOURCE` (`webcam`, `url`, `rtsp`, `file`).
5. Zero buffer lag & multi-resolution:
   - Dedicated reader thread keeping only the newest frame.
   - Provide downscaled frame for AI pipeline (YOLO) and full resolution frame for snapshots and web preview.
6. Diagnostic tools & tests:
   - Update `tools/camera_check.py` and `tools/list_cameras.py` to use new camera module.
   - Wire into `backend/live_stream.py`, `main.py`, and test suites.
   - Add unit tests in `tests/test_camera.py` (factory, reconnect backoff, watchdog, credential masking).
   - Remove obsolete `camera/rtsp_client.py`.

### Phase B: Backend Cleanup
1. Remove all 14 unused imports identified in Section 2.4.
2. Remove dead function `cosine_similarity` from `faces/matcher.py`.
3. Standardize logging in `main.py` and remove debug prints.
4. Ensure `config/settings.py` and `.env.example` are fully aligned.
5. Re-run `pytest` across all test modules to verify 100% pass rate.

### Phase C: Website Rearrangement (Liquid Glass UX)
1. Consolidate navigation into exactly **4 destinations**:
   - **Home**: Hero live-view glass card with camera status chip, "now" strip (people in view, active alert, system health), and vertical event timeline.
   - **Events**: Filter chips, thumbnail grid, lightbox with details, and integrated alert history.
   - **People**: Avatar grid, single "Add person" flow with name, 3–5 photo uploads, and mandatory consent checkbox.
   - **Settings**: Grouped sections for Camera, Polygon Zone Editor, Alerts/Telegram, Retention, and Account.
2. Reusable UI components:
   - Implement `SideRail` (desktop) and floating glass `Dock` (mobile) with glowing active indicators.
   - Refactor `GlassCard`, `Button`, `Chip`, `Modal`, `Toast`, `Skeleton`, `EmptyState`.
   - Apply strict Liquid Glass design tokens: `#F0F5F7` base, `#162623` text, `#295255` primary, `#577877` accent/glow.
3. Remove `DesignDemoPage`, `ZonesPage`, `NotificationsPage`, `OverviewPage`, and `LivePage` in favor of the clean 4-destination architecture.
4. Verify mobile-first layout (390px viewport, 44px touch targets, safe-area insets, no horizontal scroll).
5. Build and typecheck with `npm run build`.

### Phase D: Verification & Final Report
1. Run full test suite (`pytest`) and verify zero regressions.
2. Verify live camera operation with `CAMERA_SOURCE=webcam`.
3. Update `README.md` with new project structure, run instructions, and camera troubleshooting.
4. Commit changes in structured, clearly labeled Git commits.
