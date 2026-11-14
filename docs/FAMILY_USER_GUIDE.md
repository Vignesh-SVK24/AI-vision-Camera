# Family User Guide — Privacy, Consent, Enrollment & Data Deletion

Vision Surveillance is designed so **your family stays in control of your home data**:

- **Everything runs locally on your home PC.** Camera video, face detection, and face embeddings never go to third-party face-recognition clouds.
- **Strangers are never stored as face profiles.** Unrecognized people are simply marked **Unknown** on events.
- **Explicit consent is mandatory.** Nobody can be enrolled without recording the consent date, a consent note, and confirming the consent checkbox.
- **One-click / one-command deletion.** Any family member can have their face data removed immediately.

---

## 1. Before Taking Photos (Good Photo Tips)

For reliable recognition without false alerts:
- Use **2 to 5 clear photos** per person (slightly different angles help, such as straight-on and slight left/right).
- Stand in **bright, even lighting** facing the camera (avoid heavy backlighting or shadows across half the face).
- Ensure **only one person** appears in each enrollment photo.
- Avoid sunglasses, masks, motion blur, or tiny distant faces (the system automatically rejects blurry faces or faces smaller than **80 pixels**).

---

## 2. How to Enroll a Family Member (Dashboard)

1. Open the Vision Surveillance dashboard (`http://127.0.0.1:5173` or your private Tailscale HTTPS link) and sign in.
2. Open the **People** tab (top bar on desktop, bottom bar on phone).
3. Tap **Enroll Person**.
4. Fill in:
   - **Full Name**: display label shown on alerts/events (e.g., `Alex`).
   - **Consent Date**: date consent was given (defaults to today).
   - **Consent Note**: brief description (e.g., `"Alex verbally agreed on 2026-11-12 for home arrival logging"`).
   - **Photos**: select 1–10 JPG, PNG, or WebP photos (up to `5 MB` per file by default via `UPLOAD_MAX_BYTES`, even if the UI helper text mentions 10 MB).
5. Read and tick the **required consent checkbox**:
   > *"I confirm this person has provided informed consent to store their face embeddings locally for home surveillance recognition."*
6. Tap **Enroll**.
   - If a photo is blurry, shows 0 or >1 faces, or has a face under 80px, the dashboard tells you which photo was rejected and why while keeping any valid photos.

---

## 3. How to Enroll via Command Line (Optional)

```powershell
.\venv\Scripts\Activate.ps1
python tools\enroll.py add `
  --name "Alex" `
  --images photos\alex1.jpg photos\alex2.jpg `
  --consent-date 2026-11-12 `
  --consent-note "Signed household privacy consent"
```

Add `--no-save-photos` if you want to compute and store **only** the mathematical L2-normalized embedding vector in SQLite without saving JPEG copies under `faces_data/`.

---

## 4. How Alerts Treat Family vs. Unknown Visitors

With default settings in `.env`:
- **Known family members** (`ALERT_ON_KNOWN_PERSON=false`): logged quietly in the local **Events** timeline without buzzing Telegram.
- **Unknown visitors** (`ALERT_ON_UNKNOWN_PERSON=true`): trigger a high-priority alert with snapshot.
- **Restricted zone intrusions** (`ALERT_ON_ZONE_INTRUSION=true`): always trigger a high-priority alert.

You can adjust these switches anytime under the **Settings** tab in the dashboard.

---

## 5. How to Delete Someone's Data

If anyone asks to be removed (or a guest profile is no longer needed):

### Option A — In the Dashboard
1. Open the **People** tab.
2. Find the person's card and tap **Delete**.
3. Tap **Confirm Delete** in the confirmation dialog.

### Option B — From Command Line
```powershell
.\venv\Scripts\Activate.ps1
python tools\enroll.py delete --name "Alex"
# Or by numeric ID:
python tools\enroll.py delete --id 1
```

### What Deletion Removes
- The person's entry in SQLite `people` (including name and consent note)
- All biometric vectors in `face_embeddings`
- Any stored enrollment photo copies under `faces_data/`
- An entry (`api_delete_person` from the dashboard, or `delete_person` from the CLI) is written to the local `audit_log` confirming removal. Future detections of that person immediately appear as **Unknown**.
