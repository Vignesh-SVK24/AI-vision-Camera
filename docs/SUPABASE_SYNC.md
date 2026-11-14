# Optional Off-Site Cloud Backup (Supabase Free Tier)

Off-site cloud mirroring is **disabled by default** (`CLOUD_SYNC=false`) so nothing leaves your PC unless you opt in.

## Privacy Guarantees

When `CLOUD_SYNC=true`:
1. **What is uploaded:** Only minimal event rows (`local_event_id`, `camera_name`, `event_type`, timestamps, `zone_id`/`zone_name`, `confidence`, and a simple non-identifying `person_label` of `Known` or `Unknown`) plus a downscaled JPEG thumbnail (`<= 320px` wide) sourced strictly from `snapshots/`.
2. **What NEVER leaves your PC:**
   - Enrolled face photos under `faces_data/`
   - Biometric `face_embeddings` vectors
   - Enrolled people names, `consent_date`, or `consent_note` records
   - Camera RTSP credentials or local dashboard users/audit logs
3. **30-Day Expiration:** `CloudSyncWorker` runs a daily cleanup deleting both thumbnail files in Supabase Storage (`DELETE /storage/v1/object/<bucket>`) and expired event rows older than `CLOUD_SYNC_RETENTION_DAYS` (default `30`).

---

## Setup Steps (Supabase Free Tier)

1. Create a free Supabase project at https://supabase.com/.
2. Open the **SQL Editor** in Supabase and run [supabase_rls.sql](../config/supabase_rls.sql).
   - This creates the `public.events` table, creates the private `event-thumbnails` storage bucket, and enables **Row Level Security** denying all `anon` and `authenticated` public access.
3. In **Project Settings -> API**, copy:
   - **Project URL** -> `SUPABASE_URL`
   - **service_role secret key** (never the `anon` key!) -> `SUPABASE_KEY`
4. Add credentials strictly to your ignored `.env` file (never commit `.env`):
   ```ini
   CLOUD_SYNC=true
   SUPABASE_URL=https://your-project-ref.supabase.co
   SUPABASE_KEY=<your-service-role-secret-key>
   SUPABASE_BUCKET=event-thumbnails
   SUPABASE_TABLE=events
   CLOUD_SYNC_RETENTION_DAYS=30
   ```
5. Verify `.env` is ignored and no secrets exist in git-tracked files:
   ```powershell
   python tools/secret_scan.py
   ```
