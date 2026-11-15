# Vision Surveillance (Local-First Windows CPU Edition)

A privacy-first, local-first home surveillance system engineered for a **Windows 10/11 PC (CPU-only / Intel iGPU OpenVINO)**. Raw video, face recognition embeddings, and snapshots stay on your own computer; remote access is private over Tailscale or encrypted tunnels, alerts go to your Telegram bot, and optional off-site event mirroring to Supabase is **disabled by default**.

---

## Architecture Diagram

```mermaid
flowchart TB
    Cam["Camera Source<br/>(Webcam DSHOW/MSMF, RTSP TCP, HTTP, or File)"] -->|Modular Unified Interface| Cap["camera/factory.py<br/>(WebcamSource / URLStreamSource / FileSource)"]
    Cap --> Health["camera/health.py<br/>(Exponential Backoff + Stale Watchdog)"]

    subgraph PC["Windows Host PC (Services Bound to 127.0.0.1)"]
        Health --> Engine["tools/run_engine.py / main.py<br/>(GracefulShutdown + FrameWatchdog + EngineSupervisor)"]
        Engine --> Motion["detection/person_detector.py<br/>(MOG2 Motion Gate + OpenVINO YOLO11n)"]
        Motion --> Track["tracking/tracker.py + zones.py<br/>(ByteTrack + Polygon Zones)"]
        Track --> Faces["faces/face_engine.py + matcher.py<br/>(YuNet + SFace Cosine Similarity, Local Only)"]
        Faces --> Events["events/event_manager.py<br/>(Cooldowns + DiskSpaceGuard + 480px Snapshots)"]
        Events --> DB["storage/db.py (SQLite WAL + Outbox)"]
        Events --> Snaps["snapshots/YYYY-MM-DD/*.jpg"]

        DB --> TG["notifications/telegram.py<br/>(OutboxWorker + Rate Limiter)"]
        DB --> SB["storage/cloud_sync.py<br/>(Opt-In CLOUD_SYNC=true Only)"]
        DB --> API["backend/app.py (FastAPI 127.0.0.1:8000)<br/>(Argon2 + JWT + Rate Limit + Size Limit + Headers)"]
        Web["dashboard/ (React + Vite :5173)<br/>(Liquid Glass UI: Home, Events, People, Settings)"] -->|"/api/* & /ws/*"| API
    end

    TG -->|"HTTPS Bot API"| Telegram["Telegram App"]
    SB -->|"320px Thumbnails + Non-PII Rows<br/>(30-day cleanup)"| Supabase["Supabase (RLS Locked)"]
    Phone["Phone / Remote Browser"] <-->|"Tailscale WireGuard / Cloudflare Tunnel"| Web
```

---

## Data Flow Diagram

```mermaid
sequenceDiagram
    participant C as Camera Source (Webcam / RTSP / File)
    participant E as Engine Pipeline
    participant M as Motion Gate + YOLO + ByteTrack
    participant F as Local Face Matcher
    participant D as SQLite WAL + Snapshots
    participant W as Workers (Telegram / WS / Cloud Sync)

    C->>E: Latest frame packet (Downscaled for AI + Full for Preview)
    E->>M: Check MOG2 foreground motion
    alt Motion detected (at target 3-5 FPS)
        M->>M: YOLO person detection (OpenVINO) + ByteTrack ID
        M->>M: Bottom-center polygon check & dwell timer
        M->>F: Person crop -> YuNet + SFace cosine match
        F-->>E: Enrolled label or "Unknown" (stays local)
        E->>D: Check cooldowns & DiskSpaceGuard -> save <=480px JPEG + event + outbox
        D-->>W: FastAPI WebSocket pushes live event to Dashboard
        W->>W: TelegramOutboxWorker sends priority alert + snapshot (with retry/backoff)
        opt CLOUD_SYNC=true
            W->>W: CloudSyncWorker uploads <=320px thumbnail + non-PII row -> marks synced
        end
    else Scene static
        M-->>E: Skip YOLO inference (CPU stays low)
    end
```

---

## Liquid Glass Dashboard Experience

The dashboard has been simplified into **four dedicated destinations**:

1. **Home (`/`)**: Large hero live-view glass card with camera status chip, a compact "Now" strip (people in view, last alert, system health), and a vertical real-time timeline of recent events with thumbnails.
2. **Events (`/events`)**: Event activity filterable by Type, Person (Known/Unknown), and Date range. Includes an integrated lightbox modal for snapshots and a delivery history tab for Telegram alerts.
3. **People (`/people`)**: Household biometric registry with avatar grid, photo count, consent status, and a 3-5 photo enrollment modal with required privacy consent checkbox.
4. **Settings (`/settings`)**: Grouped controls for Camera & Stream health, interactive Polygon Detection Zones canvas editor (with undo, draft, and instant saving), Alert dispatch triggers, collapsible Advanced Tuning (thresholds, cooldowns, retention days), and Remote Connection configuration.

Navigation adapts seamlessly:
- **Desktop**: Slim translucent glass side rail (`SideRail`) with glow indicators on active items.
- **Mobile**: Floating glass dock (`Dock`) with 44px minimum touch targets and safe-area insets.

---

## System Requirements

- **OS:** Windows 10 or Windows 11 (64-bit)
- **CPU / Memory:** Intel Core i3/i5/i7 (integrated graphics supported via OpenVINO) or compatible x64 CPU, 8 GB+ RAM recommended
- **Python:** Python 3.11+
- **Node.js:** Node.js 20+ and npm 10+ (for the React dashboard)
- **Camera:** Built-in / USB webcam, RTSP IP camera (H.264 over TCP), HTTP stream, or local `.mp4` test video file
- **Optional Tools:** Cloudflare Tunnel or Tailscale (for remote access), VeraCrypt (at-rest encryption)

---

## Installation

Open **PowerShell** inside the project directory:

### 1. Python Virtual Environment & Dependencies

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt
```

### 2. Download Local Face Recognition Models

Downloads official OpenCV Zoo YuNet & SFace ONNX models into `models/` (stays 100% local):

```powershell
python tools\download_models.py
```

### 3. Install & Build the Frontend Dashboard

```powershell
cd dashboard
npm install
npm run build
cd ..
```

---

## Configuration (`.env`)

Create your local `.env` file from `.env.example`:

```powershell
Copy-Item .env.example .env
```

Key configuration options:

| Variable | Description | Example Values |
|---|---|---|
| `CAMERA_SOURCE` | Video input type: `webcam`, `rtsp`, `url`, or `file` | `webcam` or `file` |
| `WEBCAM_INDEX` | Windows webcam index (run `tools\list_cameras.py` to scan) | `0` |
| `WEBCAM_BACKEND` | Video capture backend | `dshow` (DirectShow) or `msmf` (Media Foundation) |
| `VIDEO_FILE_PATH` | Path to video file when `CAMERA_SOURCE=file` | `test_clips/sample_patrol.mp4` |
| `CAMERA_IP`, `CAMERA_USER`, `CAMERA_PASS` | IP camera address & RTSP credentials (when `CAMERA_SOURCE=rtsp`) | `192.168.1.100`, `admin`, `secret` |
| `JWT_SECRET_KEY` | Random secret for JWT auth tokens | Generate with `python -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `TELEGRAM_ENABLED`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | Telegram bot alerts | `true`, `123456:ABC...`, `987654` |
| `API_HOST`, `API_PORT` | FastAPI listen address | `127.0.0.1` / `8000` |
| `CORS_ALLOWED_ORIGINS` | Allowed browser origins | `http://127.0.0.1:5173,https://*.trycloudflare.com` |

---

## Camera Tools & Diagnostics

- **List connected cameras & working backends:**
  ```powershell
  python tools\list_cameras.py
  ```
- **Diagnose any camera source:**
  ```powershell
  python tools\camera_check.py --source webcam:0
  python tools\camera_check.py --source rtsp://admin:pass@192.168.1.50/stream2
  python tools\camera_check.py --source test_clips/sample_patrol.mp4
  ```

---

## Running the Application

### 1. Start the FastAPI Backend
```powershell
.\venv\Scripts\Activate.ps1
python -m backend.app
```

### 2. Start the Liquid Glass Dashboard
```powershell
cd dashboard
npm run preview -- --port 5173
```
Access at `http://127.0.0.1:5173/`.

### 3. Run the Live Vision Pipeline
```powershell
# Interactive preview with person detection and tracking:
python main.py

# Headless background execution:
python main.py --headless
```

---

## Troubleshooting

- **Windows webcam is blocked / fails to open:**
  1. Close any apps using the camera (Zoom, Teams, Skype, Windows Camera app, browser tabs).
  2. Open **Windows Settings -> Privacy & security -> Camera** and ensure **"Let desktop apps access your camera"** is toggled ON.
  3. Try switching `WEBCAM_BACKEND=msmf` instead of `dshow` in `.env`.
  4. Run `python tools\list_cameras.py` to identify if your camera is index 1 or 2 instead of 0.
  5. If the camera hardware is occupied, switch to `CAMERA_SOURCE=file` and `VIDEO_FILE_PATH=test_clips/sample_patrol.mp4` for development and testing.
- **RTSP stream stalls or drops frames:**
  The system automatically forces TCP transport (`rtsp_transport;tcp`) and features an exponential backoff reconnect watchdog. Ensure `stream2` (H.264 substream) is configured on your camera.
- **Remote Access:**
  Use Cloudflare Tunnel or Tailscale to securely expose port 8000/5173 without opening public router firewall ports. In the dashboard Settings page, enter the remote tunnel URL in the **Custom API Base URL** field.

---

## Test Verification

```powershell
# Run full unit and integration test suite:
pytest -v

# Scan for hardcoded credentials:
python tools\secret_scan.py
```
