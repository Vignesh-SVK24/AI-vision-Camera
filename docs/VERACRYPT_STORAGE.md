# Encrypting Snapshots and Database Storage with VeraCrypt

Because `surveillance.db` (SQLite), `snapshots/` (JPEG event frames), and `faces_data/` (enrolled face photos) contain sensitive home imagery and personal metadata, store them inside an encrypted **VeraCrypt** volume on Windows.

## 1. Create an Encrypted VeraCrypt Container

1. Download and install **VeraCrypt** from https://www.veracrypt.fr/ (verify the PGP signature / official SHA-256 checksum).
2. Open VeraCrypt -> **Create Volume**:
   - Choose **Create an encrypted file container** -> **Standard VeraCrypt volume**.
   - Save the container file outside the git working tree, e.g. `C:\EncryptedVaults\vision_data.hc`.
   - Select **AES** encryption + **SHA-512** hash.
   - Choose a size suited to your retention period (e.g. **10 GB** for 30 days of 480px snapshots + DB + logs).
   - Format with **NTFS** using a strong passphrase (and/or keyfile stored separately).
3. Mount `C:\EncryptedVaults\vision_data.hc` to drive letter **`V:`**.

---

## 2. Configure `.env` Paths Inside the Encrypted Volume

Create target subdirectories on `V:\`:

```powershell
New-Item -ItemType Directory -Force V:\VisionData\snapshots, V:\VisionData\faces_data, V:\VisionData\logs | Out-Null
```

Point `.env` paths to the mounted encrypted volume:

```ini
DB_PATH=V:\VisionData\surveillance.db
SNAPSHOTS_DIR=V:\VisionData\snapshots
FACES_DATA_DIR=V:\VisionData\faces_data
LOG_DIR=V:\VisionData\logs
```

> All SQLite WAL files (`surveillance.db-wal`, `surveillance.db-shm`) are created alongside `DB_PATH` inside `V:\VisionData\`, keeping every database page and snapshot ciphertext-protected when `V:` is dismounted.

---

## 3. Safe Shutdown & Dismount Procedure (SQLite WAL Integrity)

Never force-dismount `V:` while the engine or backend API is running, or uncheckpointed WAL pages may be lost.

1. Stop `main.py` (engine) and `uvicorn backend.app:app` (`Ctrl+C`) so SQLite checkpoints WAL cleanly and closes all file handles.
2. Stop `go2rtc` if running.
3. Dismount `V:` cleanly inside VeraCrypt (**Dismount** button or CLI):
   ```powershell
   & "C:\Program Files\VeraCrypt\VeraCrypt.exe" /dismount V /quit
   ```

---

## 4. Operational Hardening Tips

- **Disable Windows Search Indexing** on `V:\` (Drive Properties -> uncheck *Allow files on this drive to have contents indexed*).
- **Disable Hibernation / Encrypt Swap**: ensure BitLocker or Windows pagefile encryption (`fsutil behavior set EncryptPagingFile 1`) is enabled so cached memory isn't written unencrypted to `hiberfil.sys` or `pagefile.sys`.
- **Back up the VeraCrypt Volume Header**: in VeraCrypt select **Tools -> Backup Volume Header** and store the header + recovery passphrase offline.
