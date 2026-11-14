# Safe Remote Access with Tailscale (Free Personal Plan)

This guide enables encrypted remote access to the Vision Surveillance dashboard from your phone or laptop without port forwarding, dynamic DNS, or exposing any camera, database, or API port to the local LAN or public internet.

## Architecture Overview

```text
[Phone (Tailscale App)]
        |  WireGuard E2EE + HTTPS (https://<pc-name>.<tailnet>.ts.net)
        v
[Tailscale Serve (on Windows PC)]
        |  Proxies ONLY to localhost (127.0.0.1)
        v
[Dashboard (:5173)] ---> /api/* & /ws/* ---> [FastAPI Backend (127.0.0.1:8000)]
```

- **Zero public ports:** Router port forwarding stays completely off.
- **Zero LAN exposure:** All services bind strictly to `127.0.0.1` (enforced at startup by `validate_bind_host`) and inbound LAN/internet connections are blocked by [harden_firewall.ps1](../tools/harden_firewall.ps1).
- **RTSP isolation:** Camera credentials and RTSP (`:554`) remain strictly inside the local engine/bridge on the PC.

---

## 1. Enable Tailscale, MagicDNS, and HTTPS Certificates

1. Create a **Tailscale Personal (Free)** account at https://tailscale.com/.
2. Install Tailscale on your **Windows PC** and on your **phone** (iOS/Android), signing into the same tailnet.
3. In the Tailscale Admin Console:
   - Go to **DNS** and enable **MagicDNS**.
   - Under **HTTPS Certificates**, click **Enable HTTPS**.
   - Find your PC's private MagicDNS hostname, e.g. `home-pc.tail12345.ts.net`.
4. *(Recommended)* Restrict access in **Access Controls (ACLs)** so only your own user/devices can reach this host over the tailnet:
   ```json
   {
     "acls": [
       {
         "action": "accept",
         "src": ["autogroup:owner"],
         "dst": ["autogroup:owner:*"]
       }
     ]
   }
   ```

---

## 2. Configure `.env` for Your Private Tailscale HTTPS Origin

Update `CORS_ALLOWED_ORIGINS` in `.env` (never commit `.env`!) to include your exact MagicDNS HTTPS origin:

```ini
API_HOST=127.0.0.1
API_PORT=8000
CORS_ALLOWED_ORIGINS=http://127.0.0.1:5173,http://localhost:5173,https://home-pc.tail12345.ts.net
```

> Do **not** change `API_HOST` to `0.0.0.0`. `backend/app.py` refuses non-loopback / non-Tailscale bind hosts (`validate_bind_host`).

---

## 3. Start Local Services + Tailscale Serve (Private HTTPS)

1. Start the FastAPI backend on localhost:
   ```powershell
   .\venv\Scripts\Activate.ps1
   uvicorn backend.app:app --host 127.0.0.1 --port 8000
   ```
2. Build and preview the dashboard on localhost:
   ```powershell
   cd dashboard
   npm run build
   npm run preview -- --host 127.0.0.1 --port 5173
   ```
3. Publish the dashboard privately over HTTPS to your tailnet only:
   ```powershell
   tailscale serve --bg http://127.0.0.1:5173
   tailscale serve status
   ```
   - Tailscale automatically provisions and renews a trusted TLS certificate for `https://<pc-name>.<tailnet>.ts.net`.
   - `tailscale serve` does **not** expose the service on the public internet (unlike `tailscale funnel`, which you must **never** enable for this project).

---

## 4. Lock Down Windows Defender Firewall

Run the included firewall hardening script from an elevated PowerShell prompt to explicitly block inbound LAN and internet traffic on API, dashboard, WebRTC, and RTSP ports:

```powershell
# Preview rules without applying:
.\tools\harden_firewall.ps1 -Apply -WhatIf

# Apply inbound block rules + audit listening sockets:
.\tools\harden_firewall.ps1 -Apply

# Check active listeners and rules anytime:
.\tools\harden_firewall.ps1 -Status
```

---

## 5. Acceptance Test: External Port Scan Verification

From **another device on your LAN** (or outside your network), scan the PC's LAN IP (`<PC_LAN_IP>`) across all camera, database, bridge, and API ports:

```powershell
# PowerShell from another Windows machine on the same Wi-Fi/LAN:
554, 1984, 5173, 8000, 8555 | ForEach-Object {
    $res = Test-NetConnection -ComputerName <PC_LAN_IP> -Port $_ -WarningAction SilentlyContinue
    "{0}: TcpTestSucceeded={1}" -f $_, $res.TcpTestSucceeded
}
```

Or with `nmap` from another host:
```bash
nmap -Pn -p 554,1984,5173,8000,8555 <PC_LAN_IP>
```

**Expected result:** Every port reports `TcpTestSucceeded=False` / `filtered` or `closed` — zero open camera, database, or API ports from LAN or public internet, while `https://<pc-name>.<tailnet>.ts.net` loads over Tailscale on your phone.
