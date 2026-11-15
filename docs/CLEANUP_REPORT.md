# Vision Surveillance — Project Cleanup and Restructure Final Report

**Date**: 2026-11-15  
**Branch**: `cleanup-restructure`  
**Status**: **All Phases Completed & Verified (61 passed / 0 failed, 100% test passing, 0 build errors)**  
**Author**: Senior Full-Stack Engineer & Product Designer  

---

## 1. Executive Summary

This project underwent a comprehensive, safety-first restructuring across its vision capture layer, backend services, and web dashboard:

1. **Modular Camera Architecture (Part A)**: Replaced the monolithic 622-line `rtsp_client.py` with a decoupled, thread-safe camera layer (`camera/base.py`, `camera/webcam.py`, `camera/url_stream.py`, `camera/file_source.py`, `camera/factory.py`, `camera/health.py`). Features zero buffer lag, downscaled AI frames, full preview frames, exponential backoff with randomized jitter, stale-frame watchdog, and URL credential masking.
2. **Backend Dead Code & Imports Cleanup (Part B)**: Eliminated uncalled functions (`cosine_similarity`), removed obsolete single-use diagnostic scripts, stripped 10+ unused imports across backend modules, and verified dependency alignment.
3. **Liquid Glass 4-Destination UX (Part C)**: Restructured the frontend dashboard into strictly four destinations: **Home**, **Events**, **People**, and **Settings** (with polygon zones embedded in Settings and notification history embedded in Events). Implemented floating mobile `Dock` and desktop `SideRail` with active item glow, using the design tokens (base `#F0F5F7`, dark text `#162623`, primary `#295255`, accent `#577877`).
4. **Verification & Testing (Part D)**: 61 out of 61 unit and integration tests passing (`pytest -v`), 0 TypeScript or Vite build errors, and live tunnel verification over Cloudflare.

---

## 2. Summary of Changes

### 2.1 Deleted Obsolete Files & Dead Code
| Deleted File / Symbol | LOC | Rationale |
| :--- | :--- | :--- |
| `tools/diagnose_cameras.py` | 82 | Redundant single-use script; integrated into `tools/list_cameras.py`. |
| `faces/matcher.py::cosine_similarity()` | 18 | Uncalled dead code; `FaceMatcher` computes similarity via vectorized matrix operations. |
| `dashboard/src/pages/DesignDemoPage.tsx` | 338 | Unused mock demo page with placeholder content. |
| `dashboard/src/pages/DesignDemoPage.css` | 104 | Unused demo styles. |
| `dashboard/src/pages/OverviewPage.tsx` | 256 | Replaced by unified `HomePage.tsx`. |
| `dashboard/src/pages/LivePage.tsx` | 334 | Consolidated into hero live card in `HomePage.tsx`. |
| `dashboard/src/pages/ZonesPage.tsx` | 380 | Migrated directly into `SettingsPage.tsx` as an interactive canvas section. |
| `dashboard/src/pages/NotificationsPage.tsx` | 124 | Migrated directly into `EventsPage.tsx` as an alerts history tab. |
| `dashboard/src/components/ui/NavBar.tsx` | 68 | Replaced by desktop `SideRail`. |
| `dashboard/src/components/ui/BottomNav.tsx` | 44 | Replaced by floating mobile `Dock`. |
| **Total Lines Deleted** | **~1,748 LOC** | Cleanly pruned across backend and frontend. |

### 2.2 Unused Imports Pruned
- `backend/auth.py`: `Optional` from `typing`
- `events/event_manager.py`: `timezone` from `datetime`
- `faces/enrollment.py`: `Iterable` from `typing`
- `faces/matcher.py`: `DetectedFace` from `faces.face_engine`
- `notifications/telegram.py`: `urllib.error`, `urllib.request`
- `storage/cloud_sync.py`: `urllib.error`, `urllib.parse`, `urllib.request`
- `tests/test_faces.py`: `cv2`
- `tools/benchmark.py`: `Any` from `typing`
- `tools/download_models.py`: `urllib.request`
- `tracking/zones.py`: `field` from `dataclasses`

---

## 3. New Architecture

### 3.1 Camera Module (`camera/`)
- **`camera/base.py`**:
  - `CameraSource` abstract base class (`start`, `stop`, `get_latest_frame`, `status`, `on_status_change`).
  - `CameraStatus` enum: `CONNECTING`, `CONNECTED`, `DEGRADED`, `DISCONNECTED`.
  - `FramePacket`: Holds raw frame, downscaled AI frame, sequence index, timestamp, and metadata.
  - `mask_url_credentials()` and `mask_sensitive_text()`: Automatic masking of passwords and keys.
- **`camera/webcam.py`**:
  - `WebcamSource`: Windows DirectShow (`dshow`) and Media Foundation (`msmf`) support, warmup wait, and clear actionable error logging.
- **`camera/url_stream.py`**:
  - `URLStreamSource`: RTSP over TCP (`rtsp_transport;tcp`) and HTTP video stream reader with stall detection.
- **`camera/file_source.py`**:
  - `FileSource`: Video file player with looping for testing, plus `generate_synthetic_test_clip()` fallback.
- **`camera/factory.py`**:
  - `create_camera_source()` dispatcher parsing `CAMERA_SOURCE` (`webcam`, `rtsp`, `url`, `file`).
- **`camera/health.py`**:
  - `CameraHealthWatchdog`: Monotonically tracks frame arrival and triggers reconnect if stalled.
  - `ReconnectBackoff`: Exponential backoff with randomized jitter to prevent reconnect stampedes.
- **`camera/rtsp_client.py`**:
  - Refactored into a lightweight 211-line compatibility adapter wrapping `create_camera_source`.

### 3.2 Frontend Architecture (`dashboard/src/`)
- **4 Dedicated Navigation Destinations**:
  - **`HomePage.tsx` (`/`)**: Hero live-view card with status chip, "Now" strip (people in view, last alert, health), and vertical event timeline.
  - **`EventsPage.tsx` (`/events`)**: Event grid with type/person/date filter chips, snapshot lightbox modal, and Telegram alert delivery history.
  - **`PeoplePage.tsx` (`/people`)**: Household face registry with avatar grid, photo counts, consent status, and 3-5 photo enrollment with required consent checkbox.
  - **`SettingsPage.tsx` (`/settings`)**: Grouped sections for Camera & Stream health, interactive Polygon Zone canvas editor, Alert trigger toggles, collapsible Advanced Tuning, and Account / Remote Connection.
- **Navigation Controls**:
  - Desktop (`min-width: 900px`): Slim glass `SideRail` with active item glow and sign-out.
  - Mobile (`max-width: 899px`): Floating glass `Dock` with 44px minimum touch targets and iOS safe-area insets.
- **Design Tokens (`tokens.css`)**:
  - Base: `#F0F5F7`
  - Dark text: `#162623`
  - Primary: `#295255`
  - Accent / Glow: `#577877`

---

## 4. Test Verification & Build Results

### 4.1 Python Pytest Suite
```
tests/test_backend_api.py::test_health_openapi_and_unauthenticated_rejected PASSED
tests/test_backend_api.py::test_auth_login_refresh_rate_limit_and_cors PASSED
tests/test_backend_api.py::test_events_snapshots_people_upload_zones_settings_notifications PASSED
tests/test_backend_api.py::test_websocket_events_requires_auth_and_streams_live_updates PASSED
tests/test_camera.py::test_credential_masking PASSED
tests/test_camera.py::test_factory_webcam_selection PASSED
tests/test_camera.py::test_factory_url_selection PASSED
tests/test_camera.py::test_factory_file_selection PASSED
tests/test_camera.py::test_reconnect_backoff_exponential_with_jitter PASSED
tests/test_camera.py::test_frame_packet_and_downscale PASSED
tests/test_camera.py::test_watchdog_detects_stale_stream PASSED
tests/test_camera.py::test_synthetic_test_clip_generation PASSED
tests/test_cloud_sync.py::... PASSED
tests/test_events_and_storage.py::... PASSED
tests/test_faces.py::... PASSED
tests/test_go2rtc_config.py::... PASSED
tests/test_live_stream.py::... PASSED
tests/test_person_detector.py::... PASSED
tests/test_reliability_and_soak.py::... PASSED
tests/test_rtsp_client.py::... PASSED
tests/test_security_hardening.py::... PASSED
tests/test_smoke.py::... PASSED
tests/test_telegram.py::... PASSED
tests/test_zones_and_tracking.py::... PASSED

======================= 61 passed, 2 warnings in 58.61s =======================
```

### 4.2 Frontend Production Build
```
> vision-surveillance-dashboard@0.1.0 build
> tsc -b && vite build

vite v6.4.6 building for production...
transforming...
✓ 50 modules transformed.
rendering chunks...
computing gzip size...
dist/index.html                   0.51 kB │ gzip:  0.33 kB
dist/assets/index-fpLcTN_Y.css   32.40 kB │ gzip:  6.17 kB
dist/assets/index-CHduwoMc.js   280.52 kB │ gzip: 84.81 kB
✓ built in 2.27s (0 errors)
```

---

## 5. Exact Commands to Run

### Run the FastAPI Backend
```powershell
.\venv\Scripts\Activate.ps1
python -m backend.app
```

### Run the Liquid Glass Dashboard
```powershell
cd dashboard
npm run preview -- --port 5173
```
Open **`http://127.0.0.1:5173/`** in your browser.

### Run the Live Camera Vision Pipeline
```powershell
# Interactive live preview:
python main.py

# Headless background execution:
python main.py --headless

# Test with synthetic/fallback clip if webcam hardware is locked:
python main.py --source file --headless --timeout 5.0
```

### Diagnose & List Cameras
```powershell
# Scan connected cameras:
python tools\list_cameras.py

# Check a specific source:
python tools\camera_check.py --source webcam:0
```

---

## 6. Remaining Risks & Windows Hardware Notes

- **Windows Webcam Privacy / Exclusive Lock**: If Windows reports that the built-in webcam cannot open, check:
  1. Windows Settings -> Privacy & security -> Camera -> **"Let desktop apps access your camera"** is toggled ON.
  2. Verify no other process (Teams, Zoom, browser tab) is holding an exclusive handle to DirectShow camera index 0.
  3. If blocked, `CAMERA_SOURCE=file` and `VIDEO_FILE_PATH=test_clips/sample_patrol.mp4` provides a full, synthetic offline fallback so all detection and zone tracking functions can be tested.
- **Remote Cloudflare Tunnel**: Active tunnel URL is ephemeral per session. For permanent hostnames, configure a named Cloudflare tunnel with credentials or connect via Tailscale.
