# Threat Model & Security Acceptance Checklist

## 1. Protected Assets

| Asset | Location | Sensitivity |
|---|---|---|
| Camera RTSP credentials | `.env` (`CAMERA_USER`, `CAMERA_PASS`) | High (LAN camera feed takeover) |
| Owner JWT signing key & password hashes | `.env` (`JWT_SECRET_KEY`), `users` table | Critical (API & dashboard auth) |
| Telegram bot token & chat ID | `.env` (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`) | High (alert spoofing / channel access) |
| Event database & face embeddings | `surveillance.db*` (`face_embeddings`, `events`) | High (biometric & home activity PII) |
| Event & enrollment snapshots | `snapshots/`, `faces_data/` | High (household & visitor imagery) |
| Local RTSP & go2rtc streams | Camera `:554`, go2rtc `127.0.0.1:1984` | High (unauthenticated live video) |

## 2. Trust Boundaries

1. **Public Internet <-> Home Router**: Zero inbound ports forwarded; no public exposure.
2. **LAN <-> Windows Host**: Camera RTSP is pulled outbound over TCP; inbound connections to host services (`8000`, `5173`, `1984`, `8555`, `554`) are blocked by localhost binding + Windows Firewall (`tools/harden_firewall.ps1`).
3. **Tailnet Device <-> Windows Host (Tailscale Serve)**: Authenticated WireGuard tunnel terminating on HTTPS (`tailscale serve`), proxied only to `127.0.0.1:5173` (and never `tailscale funnel`).
4. **Browser <-> FastAPI Backend**: Protected by Bearer JWT access/refresh tokens, strict CORS allowlist, security headers, size limits, and consistent per-IP/tier rate limiting.

## 3. STRIDE Threat Matrix & Controls

| Threat | Vector | Implemented Control | Code / Doc Reference |
|---|---|---|---|
| **Spoofing** | Brute-force `/auth/login` or `/auth/refresh` | Argon2id password hashing + per-IP/username failed-login lockout (`LoginRateLimiter`) + consistent middleware rate limiting (`429` + `Retry-After`) + no open user registration | `backend/auth.py`, `backend/security.py`, `backend/app.py` |
| **Tampering** | Oversized JSON payloads / upload bombs / chunked flood | `RequestSizeLimitMiddleware` checking `Content-Length` and streamed body bytes (`413` over limit) + `validate_image_upload` (extension, MIME, magic bytes, `cv2.imdecode`) | `backend/security.py` |
| **Repudiation** | Unauthorized logins, enrollment, deletes, settings/zone edits | Immutable `audit_log` records actions, actors, client IP, and timestamps | `storage/db.py`, `backend/app.py` |
| **Information Disclosure** | Leaked secrets in logs, repo, browser cache, or open ports | `mask_sensitive_text` log filter; `.gitignore` + `tools/secret_scan.py`; `Cache-Control: no-store` & `X-Frame-Options: DENY` headers; `127.0.0.1` service binds + `validate_bind_host`; VeraCrypt storage guide | `config/logging_config.py`, `backend/security.py`, `tools/secret_scan.py`, `docs/VERACRYPT_STORAGE.md` |
| **Denial of Service** | Request floods on read/write endpoints or RTSP reconnect storms | `ConsistentRateLimitMiddleware` (read/write/auth tiers across all routes) + RTSP exponential backoff + motion-gated YOLO | `backend/security.py`, `camera/rtsp_client.py` |
| **Elevation of Privilege** | Path traversal on `/snapshots/{id}` or arbitrary `settings` overwrite | `resolve_snapshot_path` containment check; `ALLOWED_SETTING_KEYS` allowlist; Pydantic schemas | `storage/snapshots.py`, `backend/app.py` |

## 4. Residual Risks & Operational Assumptions

- **Tailscale Account Compromise**: Whoever controls your Tailscale account/tailnet can reach `tailscale serve`. Protect Tailscale login with phishing-resistant MFA and restrictive Tailscale ACLs.
- **Physical Disk Access While VeraCrypt Volume Is Mounted**: Encryption protects data at rest when dismounted or powered off; lock Windows (`Win+L`) when unattended.
- **Dependency Vulnerabilities**: Run `pip-audit` and `npm audit` on every dependency update (see `docs/DEPENDENCY_AUDIT.md`).

---

## 5. Security Acceptance Checklist

- [x] Every backend and local streaming service defaults to `127.0.0.1` (`validate_bind_host` rejects `0.0.0.0` / LAN / public IPs).
- [x] `config/go2rtc.yaml` sets `api.listen: "127.0.0.1:1984"` and disables RTSP re-streaming (`rtsp.listen: ""`).
- [x] Windows Firewall script ([harden_firewall.ps1](../tools/harden_firewall.ps1)) blocks inbound LAN and internet traffic on ports `8000, 5173, 1984, 8555, 554`.
- [x] Security headers (`X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `Permissions-Policy`, `Cross-Origin-Resource-Policy`, `Content-Security-Policy`, `Cache-Control: no-store`, conditional `HSTS`) added to every HTTP response.
- [x] Request size limits enforced at ASGI layer (`RequestSizeLimitMiddleware`, header + chunked body accounting) and per-file upload validation.
- [x] Consistent rate limiting enforced across auth, write, and read routes (`ConsistentRateLimitMiddleware` + `LoginRateLimiter`).
- [x] `.gitignore` verified to ignore `.env`, `.env.*`, `*.db`, `snapshots/`, `faces_data/`, `models/`, `venv/`; zero secrets detected by `tools/secret_scan.py`.
- [x] Remote Tailscale HTTPS access documented (`docs/REMOTE_ACCESS.md`) without `tailscale funnel` or router port forwarding.
- [x] VeraCrypt storage encryption and safe SQLite WAL dismount workflow documented (`docs/VERACRYPT_STORAGE.md`).
- [ ] Run `pip-audit` and `npm audit` after installing dependencies (pending dependency installation approval; see `docs/DEPENDENCY_AUDIT.md`).
- [ ] Run external LAN port scan against `<PC_LAN_IP>` (`nmap -Pn -p 554,1984,5173,8000,8555 <PC_LAN_IP>`) after applying `harden_firewall.ps1` and confirm zero open ports.
