# Instagram Cleanup

Script Python untuk menghapus postingan Instagram **akun milik sendiri** secara bertahap
(default 5 postingan per jam, jeda acak antar postingan), dengan browser otomatis
([Playwright](https://playwright.dev/python/)). Dirancang agar bisa dijalankan tanpa interaksi
oleh bot/scheduler seperti [OpenClaw](https://docs.openclaw.ai/).

> ## ⚠️ Peringatan
> - **Penghapusan bersifat permanen dan tidak bisa dibatalkan.** Selalu mulai dengan dry-run.
> - Gunakan **hanya untuk akun milik Anda sendiri**.
> - Mengotomatisasi Instagram dapat melanggar Ketentuan Layanan Instagram dan berisiko
>   membuat akun dibatasi atau diblokir. Gunakan dengan risiko sendiri. Script ini sengaja
>   lambat (batas per jam + jeda acak) untuk mengurangi risiko, tapi tidak ada jaminan.
> - Script tidak mencoba melewati captcha / security check. Jika terdeteksi, script berhenti.
> - Folder `instagram_browser_profile/` berisi sesi login Anda. **Jangan di-commit atau dibagikan.**

## Fitur

- Dry-run secara default; hapus sungguhan butuh `--execute --confirm DELETE`
- Batas 1 batch per jam, maksimal `--limit` postingan per batch
- Jeda acak antar postingan (`--min-delay` / `--max-delay`)
- Checkpoint: postingan yang sudah dihapus tidak diproses ulang
- Berhenti otomatis saat login habis atau ada security check
- Output mudah di-parse bot: baris terakhir `RESULT: {json}` + exit code
- Lock file agar tidak ada dua proses berjalan bersamaan

## 1. Persiapan

Kebutuhan: Python 3.9+, Git, dan (opsional) [OpenClaw](https://docs.openclaw.ai/) untuk penjadwalan.

```bash
git clone https://github.com/<username-anda>/instagram_cleanup.git
cd instagram_cleanup

python -m venv .venv
# Windows (PowerShell):  .venv\Scripts\Activate.ps1
# macOS/Linux:           source .venv/bin/activate

pip install -r requirements.txt
playwright install chromium
```

> Kalau Anda tidak memakai virtualenv, di langkah OpenClaw nanti gunakan **path absolut**
> ke `python` yang dipakai saat `pip install`.

## 2. Login Instagram (sekali saja)

Jalankan dengan jendela browser terlihat, lalu login manual (termasuk 2FA bila ada).
Jendela tetap terbuka maksimal 20 menit dan menutup sendiri setelah login terdeteksi.
**Jangan ditutup manual.**

```bash
python instagram_cleanup.py --username NAMA_AKUN_ANDA --login
```

Sesi tersimpan di `instagram_browser_profile/`. Jika nanti sesi habis, ulangi langkah ini.

## 3. Dry-run (wajib sebelum menghapus)

```bash
python instagram_cleanup.py --username NAMA_AKUN_ANDA --headless
```

Script hanya membuka postingan dan mencatat apa yang *akan* dihapus. Tidak ada yang dihapus
dan checkpoint tidak berubah. Pastikan daftar postingan di log sesuai harapan.

## 4. Tes hapus 1 postingan

Postingan **paling baru** akan terhapus permanen:

```bash
python instagram_cleanup.py --username NAMA_AKUN_ANDA --headless --execute --confirm DELETE --limit 1
```

Pastikan postingan benar-benar terhapus di Instagram. Instagram sesekali mengubah tampilannya;
jika menu/tombol hapus tidak ditemukan, script mencatatnya sebagai gagal di log.

> Batas 1 jam berlaku sejak batch hapus terakhir. Untuk mengulang sebelum 1 jam, hapus nilai
> `last_batch_time` (set `null`) di `instagram_checkpoint.json`.

## 5. Opsi

| Opsi | Keterangan |
|---|---|
| `--username` | Username Instagram (atau env `INSTAGRAM_USERNAME`). **Wajib.** |
| `--login` | Login manual sekali (membuka jendela browser). |
| `--headless` | Browser tanpa jendela (untuk bot/scheduler). |
| `--execute` | Hapus sungguhan. Tanpa ini = dry-run. |
| `--confirm DELETE` | Wajib bersama `--execute`. |
| `--limit N` | Maks postingan per batch (default 5). |
| `--min-delay` / `--max-delay` | Jeda acak antar postingan, detik (default 5–10). |
| `--wait` | Tunggu sampai interval 1 jam terpenuhi, bukan langsung keluar kode 3. |

### Exit code

| Kode | Arti |
|---|---|
| 0 | Sukses (termasuk "tidak ada yang perlu diproses") |
| 1 | Error tak terduga |
| 2 | Perlu login — jalankan lagi dengan `--login` |
| 3 | Belum 1 jam sejak batch hapus terakhir |
| 4 | Security check / captcha terdeteksi — tangani manual |
| 5 | `--confirm DELETE` tidak diberikan |
| 6 | Instance lain sedang berjalan |

Baris terakhir stdout selalu berbentuk:

```
RESULT: {"status": "done", "mode": "delete", "processed": 5, "failed": 0, "total_deleted": 12}
```

## 6. Menjadwalkan dengan OpenClaw

[OpenClaw](https://docs.openclaw.ai/) menjalankan scheduler ("cron") di Gateway-nya. Pastikan OpenClaw
sudah terpasang dan Gateway berjalan:

```bash
openclaw cron status
```

> Jadwal hanya berjalan selama **Gateway OpenClaw hidup**. Jika PC/server mati, jadwal berhenti
> dan lanjut lagi saat menyala.

### 6.1 Cari tujuan laporan Telegram (opsional)

Agar hasil tiap run dikirim ke Telegram lewat bot Anda:

1. Kirim satu pesan ke bot Telegram Anda (selesaikan pairing bila diminta).
2. Cari chat ID Anda dan nama *account* bot di OpenClaw, misalnya:

   ```bash
   openclaw agents list --bindings
   openclaw sessions --agent <nama-agent> --json
   ```

   Pada bagian `participants`, entri dengan `pluginId: "telegram"` memuat `accountId`
   (nama account bot) dan `id` (chat ID Anda).

### 6.2 Buat cron job

Gunakan **`--command-argv`** supaya Python dijalankan langsung oleh Gateway tanpa lewat model AI
(lebih andal dan tidak bisa "salah menjalankan" perintah). Ganti semua nilai di dalam `< >`.

**PowerShell (Windows):**

```powershell
$argv = ConvertTo-Json -Compress @(
  "C:\path\ke\python.exe",
  "C:\path\ke\instagram_cleanup\instagram_cleanup.py",
  "--username", "NAMA_AKUN_ANDA",
  "--headless", "--execute", "--confirm", "DELETE",
  "--min-delay", "120", "--max-delay", "300"
)

openclaw cron add `
  --name "instagram-cleanup" `
  --description "Hapus postingan Instagram per jam" `
  --agent <nama-agent> `
  --every 1h `
  --session isolated `
  --command-argv $argv `
  --command-cwd "C:\path\ke\instagram_cleanup" `
  --timeout-seconds 1800 `
  --announce --channel telegram --account <nama-account-bot> --to <chatId> `
  --best-effort-deliver
```

**bash (macOS/Linux):**

```bash
openclaw cron add \
  --name "instagram-cleanup" \
  --description "Hapus postingan Instagram per jam" \
  --agent <nama-agent> \
  --every 1h \
  --session isolated \
  --command-argv '["/path/ke/python","/path/ke/instagram_cleanup/instagram_cleanup.py","--username","NAMA_AKUN_ANDA","--headless","--execute","--confirm","DELETE","--min-delay","120","--max-delay","300"]' \
  --command-cwd /path/ke/instagram_cleanup \
  --timeout-seconds 1800 \
  --announce --channel telegram --account <nama-account-bot> --to <chatId> \
  --best-effort-deliver
```

Catatan:
- Tanpa laporan Telegram, hapus `--announce --channel ... --account ... --to ...`.
- Dengan jeda 120–300 detik, satu batch 5 postingan butuh ±8–20 menit, jadi
  `--timeout-seconds 1800` (30 menit) dipakai agar tidak terpotong.
- Ingin mencoba dulu tanpa menghapus? Buang `--execute --confirm DELETE`, atau tambahkan
  `--disabled` pada `cron add` lalu aktifkan setelah siap.

### 6.3 Kelola job

```bash
openclaw cron list                       # lihat semua job & jadwal berikutnya
openclaw cron runs --id <job-id>         # riwayat run (status, exit code, output)
openclaw cron run <job-id>               # jalankan sekarang (debug)
openclaw cron disable <job-id>           # jeda
openclaw cron enable <job-id>            # lanjutkan
openclaw cron rm <job-id>                # hapus job
```

`openclaw cron run` menjalankan script **sungguhan**: jika perintahnya berisi
`--execute --confirm DELETE`, postingan benar-benar terhapus (selama batas 1 jam terpenuhi).

### 6.4 Alternatif: Task Scheduler / cron biasa

Tanpa OpenClaw, jadwalkan perintah yang sama lewat Windows Task Scheduler atau `cron`:

```bash
0 * * * * /path/ke/python /path/ke/instagram_cleanup.py --username NAMA_AKUN_ANDA --headless --execute --confirm DELETE
```

## Pemecahan masalah

| Gejala | Penyebab / solusi |
|---|---|
| `Executable doesn't exist ... chrome-headless-shell` | Jalankan `playwright install chromium`. |
| `login_required` (exit 2) | Sesi habis. Jalankan ulang `--login`. |
| `security_check` (exit 4) | Instagram meminta verifikasi. Selesaikan manual di browser, tunggu beberapa jam, lalu lanjutkan. Jangan memaksa. |
| `too_early` (exit 3) | Belum 1 jam sejak batch terakhir. Normal; OpenClaw menandainya sebagai error biasa. |
| `locked` (exit 6) | Ada proses lain berjalan, atau sisa crash. Hapus `instagram_cleanup.lock` bila yakin tak ada proses aktif (lock basi otomatis diabaikan setelah 3 jam). |
| "Menu postingan / tombol Delete tidak ditemukan" | Tampilan Instagram berubah atau bahasa akun bukan Inggris/Indonesia. Sesuaikan selector di `open_post_menu` dan `find_delete_button`. |
| `DataCloneError` pada job agent OpenClaw | Gunakan `--command-argv` (seperti di atas), bukan job berbasis pesan agent. |

## Lisensi

Tambahkan file `LICENSE` sesuai pilihan Anda (mis. MIT) sebelum dipublikasikan.
