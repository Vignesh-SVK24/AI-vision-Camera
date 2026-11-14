# Dependency Security Audit Report (`pip-audit` & `npm audit`)

## Audit Status Summary

Because workspace rules require **explicit user approval before installing new packages**:
- `pip-audit` has **not** yet been installed in the Python environment.
- `dashboard/node_modules` and `dashboard/package-lock.json` have **not** yet been created via `npm install`.

Accordingly, automated CVE database lookups (`pip-audit` and `npm audit`) are **pending approval to install dependencies**. Below is the exact baseline of packages currently present and declared, along with the commands to execute immediately after approval.

---

## 1. Python Environment Baseline

### Currently Installed in System Python 3.11.4 (`pip list` snapshot)
- Web / ASGI stack: `fastapi==0.135.2`, `starlette==1.0.0`, `uvicorn==0.42.0`, `httpx==0.28.1`, `httpcore==1.0.9`, `h11==0.16.0`, `anyio==4.13.0`, `pydantic==2.12.5`, `pydantic_core==2.41.5`, `python-multipart==0.0.22`, `aiofiles==25.1.0`, `certifi==2026.2.25`, `idna==3.11`.
- Project `requirements.txt` declarations: `opencv-python`, `ultralytics`, `numpy`, `python-dotenv`, `pytest`, `fastapi`, `uvicorn`, `python-multipart`, `httpx`, `argon2-cffi`, `PyJWT`, `openvino`, `psutil`.

### Static Recommendations (Before Installing)
- Ensure `requirements.txt` installations run inside an isolated `venv/` and run `pip-audit` immediately afterward to verify no vulnerable versions of `starlette`, `python-multipart`, `PyJWT`, `pillow`, or `ultralytics` transitive dependencies are pulled.
- After `pip install -r requirements.txt`, freeze exact audited versions (`pip freeze > requirements.lock.txt`) for reproducible builds.

### Commands to Run `pip-audit` (Once Approved)
```powershell
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt pip-audit
pip-audit -r requirements.txt
```

---

## 2. Frontend Dashboard Baseline (`dashboard/package.json`)

- Runtime dependencies (minimal surface area — only 2 direct runtime packages):
  - `react: ^19.0.0`
  - `react-dom: ^19.0.0`
- Dev-only build dependencies:
  - `@types/react: ^19.0.0`
  - `@types/react-dom: ^19.0.0`
  - `@vitejs/plugin-react: ^4.3.4`
  - `typescript: ^5.7.2`
  - `vite: ^6.0.0`

### Commands to Generate Lockfile & Run `npm audit` (Once Approved)
```powershell
cd dashboard
npm install
npm audit --audit-level=moderate
```
If `npm audit` reports advisories in `vite` or `esbuild` dev-server dependencies, run `npm audit fix` (without `--force` first) and re-run `npm run build`.
