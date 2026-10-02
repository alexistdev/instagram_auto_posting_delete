"""
Instagram cleanup — dirancang agar bisa dijalankan non-interaktif oleh bot
(luluk via OpenClaw).

Contoh pemakaian:

  # Login manual sekali saja (butuh jendela browser, dijalankan manusia)
  python instagram_cleanup.py --login

  # Simulasi (default, tidak menghapus apa pun)
  python instagram_cleanup.py --headless

  # Hapus sungguhan: butuh --execute DAN --confirm DELETE
  python instagram_cleanup.py --headless --execute --confirm DELETE

Baris terakhir stdout selalu berupa satu objek JSON (prefix "RESULT: ")
yang bisa di-parse bot. Exit code:

  0 = sukses (termasuk "tidak ada yang perlu diproses")
  1 = error tak terduga
  2 = perlu login (jalankan dengan --login)
  3 = belum 1 jam sejak batch terakhir (lihat next_batch_time)
  4 = security check / captcha terdeteksi (hentikan, tangani manual)
  5 = konfirmasi --confirm DELETE tidak diberikan
  6 = instance lain sedang berjalan
"""

import argparse
import json
import os
import random
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from playwright.sync_api import sync_playwright


# ============================================================
# CONFIGURATION
# ============================================================

BASE_DIR = Path(__file__).resolve().parent

DEFAULT_USERNAME = os.environ.get("INSTAGRAM_USERNAME", "")
DEFAULT_POSTS_PER_HOUR = 5
DEFAULT_MIN_DELAY = 5
DEFAULT_MAX_DELAY = 10

BROWSER_PROFILE = BASE_DIR / "instagram_browser_profile"
CHECKPOINT_FILE = BASE_DIR / "instagram_checkpoint.json"
LOG_FILE = BASE_DIR / "instagram_cleanup.log"
LOCK_FILE = BASE_DIR / "instagram_cleanup.lock"

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_LOGIN_REQUIRED = 2
EXIT_TOO_EARLY = 3
EXIT_SECURITY = 4
EXIT_NOT_CONFIRMED = 5
EXIT_LOCKED = 6


class CleanupExit(Exception):
    def __init__(self, code, status, **extra):
        super().__init__(status)
        self.code = code
        self.status = status
        self.extra = extra


# ============================================================
# LOGGING
# ============================================================

def log(message):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {message}"

    print(line, flush=True)

    with open(LOG_FILE, "a", encoding="utf-8") as file:
        file.write(line + "\n")


# ============================================================
# LOCK (cegah dua bot/proses berjalan bersamaan)
# ============================================================

def acquire_lock():
    try:
        fd = os.open(LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        # Lock basi (> 3 jam) dianggap sisa proses yang crash.
        age = time.time() - LOCK_FILE.stat().st_mtime
        if age < 3 * 3600:
            raise CleanupExit(EXIT_LOCKED, "locked")
        LOCK_FILE.unlink(missing_ok=True)
        fd = os.open(LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)

    with os.fdopen(fd, "w") as file:
        file.write(str(os.getpid()))


def release_lock():
    LOCK_FILE.unlink(missing_ok=True)


# ============================================================
# CHECKPOINT
# ============================================================

def create_empty_checkpoint():
    return {
        "processed": [],
        "deleted": [],
        "failed": [],
        "last_batch_time": None,
    }


def load_checkpoint():
    if not CHECKPOINT_FILE.exists():
        return create_empty_checkpoint()

    try:
        with open(CHECKPOINT_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)

        data.setdefault("processed", [])
        data.setdefault("deleted", [])
        data.setdefault("failed", [])
        data.setdefault("last_batch_time", None)

        return data

    except Exception as error:
        log(f"Checkpoint error: {error}")
        return create_empty_checkpoint()


def save_checkpoint(data):
    temporary_file = CHECKPOINT_FILE.with_suffix(".tmp")

    with open(temporary_file, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=2, ensure_ascii=False)

    os.replace(temporary_file, CHECKPOINT_FILE)


# ============================================================
# HOURLY LIMIT
# ============================================================

def next_batch_time(checkpoint):
    last_batch_time = checkpoint.get("last_batch_time")

    if not last_batch_time:
        return None

    try:
        last_time = datetime.fromisoformat(last_batch_time)
    except ValueError:
        return None

    return last_time + timedelta(hours=1)


def wait_until(target):
    while True:
        seconds = (target - datetime.now()).total_seconds()

        if seconds <= 0:
            return

        log(f"Batch berikutnya dalam {int(seconds / 60)} menit.")
        time.sleep(min(60, seconds))


# ============================================================
# RANDOM DELAY
# ============================================================

def human_delay(args):
    delay = random.uniform(args.min_delay, args.max_delay)
    log(f"Menunggu {delay:.1f} detik.")
    time.sleep(delay)


# ============================================================
# LOGIN
# ============================================================

LOGIN_TIMEOUT_SECONDS = 20 * 60


def has_session(context):
    return any(
        cookie["name"] == "sessionid" and "instagram.com" in cookie["domain"]
        for cookie in context.cookies()
    )


def ensure_login(page, context, args):
    if args.login:
        page.goto(
            "https://www.instagram.com/accounts/login/",
            wait_until="domcontentloaded",
        )
        log(
            "Silakan login di jendela browser. Jendela akan tetap terbuka "
            f"sampai login terdeteksi (maks {LOGIN_TIMEOUT_SECONDS // 60} menit)."
        )

        deadline = time.time() + LOGIN_TIMEOUT_SECONDS

        while time.time() < deadline:
            if has_session(context):
                time.sleep(10)  # beri waktu cookie tersimpan ke profile
                return
            time.sleep(2)

        raise CleanupExit(EXIT_LOGIN_REQUIRED, "login_timeout")

    page.goto("https://www.instagram.com/", wait_until="domcontentloaded")
    time.sleep(4)

    if not has_session(context) or "/accounts/login" in page.url.lower():
        log("Login Instagram diperlukan.")
        raise CleanupExit(EXIT_LOGIN_REQUIRED, "login_required")


# ============================================================
# SECURITY CHECK
# ============================================================

SECURITY_INDICATORS = [
    "captcha",
    "security check",
    "confirm it's you",
    "suspicious login",
    "suspicious activity",
    "challenge_required",
    "verify your identity",
    "verify it's you",
    "checkpoint",
]


def check_security(page):
    try:
        url = page.url.lower()
        body_text = page.locator("body").inner_text().lower()
    except Exception:
        return True

    for indicator in SECURITY_INDICATORS:
        if indicator in url or indicator in body_text:
            log(f"Security check terdeteksi: {indicator}")
            return False

    return True


def require_security(page):
    if not check_security(page):
        raise CleanupExit(EXIT_SECURITY, "security_check")


# ============================================================
# COLLECT POST URLS
# ============================================================

def collect_post_urls(page, args, limit):
    log(f"Mengambil postingan. Target: {limit}")

    page.goto(
        f"https://www.instagram.com/{args.username}/",
        wait_until="domcontentloaded",
    )
    time.sleep(4)

    require_security(page)

    urls = {}
    previous_height = 0

    for _ in range(20):
        require_security(page)

        links = page.locator('a[href*="/p/"], a[href*="/reel/"]')

        try:
            count = links.count()
        except Exception:
            count = 0

        for index in range(count):
            try:
                href = links.nth(index).get_attribute("href")

                if not href or ("/p/" not in href and "/reel/" not in href):
                    continue

                if href.startswith("/"):
                    href = "https://www.instagram.com" + href

                urls[href] = None  # dict menjaga urutan (terbaru dulu)

            except Exception:
                continue

        if len(urls) >= limit:
            break

        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(2)

        try:
            current_height = page.evaluate("document.body.scrollHeight")
        except Exception:
            break

        if current_height == previous_height:
            break

        previous_height = current_height

    result = list(urls)
    log(f"Ditemukan {len(result)} postingan.")

    return result


# ============================================================
# POST MENU / DELETE BUTTON
# ============================================================

def open_post_menu(page):
    selectors = [
        'button[aria-label="More options"]',
        'button[aria-label="More"]',
        'svg[aria-label="More options"]',
        'svg[aria-label="More"]',
    ]

    for selector in selectors:
        try:
            locator = page.locator(selector)

            if locator.count() > 0:
                locator.first.click()
                time.sleep(1)
                return True

        except Exception:
            continue

    return False


def find_delete_button(page):
    for selector in ['text="Delete"', 'text="Hapus"']:
        try:
            locator = page.locator(selector)

            if locator.count() > 0:
                return locator.last

        except Exception:
            continue

    return None


# ============================================================
# DELETE POST
# ============================================================

def delete_post(page, post_url, args):
    log(f"Memproses: {post_url}")

    page.goto(post_url, wait_until="domcontentloaded")
    time.sleep(3)

    require_security(page)

    if not args.execute:
        log("[DRY RUN] Tidak menghapus.")
        return True

    if not open_post_menu(page):
        log("Menu postingan tidak ditemukan.")
        return False

    time.sleep(1)

    delete_button = find_delete_button(page)

    if delete_button is None:
        log("Tombol Delete/Hapus tidak ditemukan.")
        return False

    delete_button.click()
    time.sleep(2)

    require_security(page)

    confirm_button = find_delete_button(page)

    if confirm_button is None:
        log("Konfirmasi Delete tidak ditemukan.")
        return False

    confirm_button.click()
    time.sleep(3)

    log("Postingan berhasil dihapus.")
    return True


# ============================================================
# PROCESS BATCH
# ============================================================

def process_batch(page, checkpoint, post_urls, args):
    ok = 0
    failed = 0

    for index, url in enumerate(post_urls, start=1):
        log(f"Processing {index}/{len(post_urls)}")

        try:
            success = delete_post(page, url, args)

            if success:
                ok += 1

                # Dry run tidak boleh mengubah checkpoint.
                if args.execute:
                    if url not in checkpoint["processed"]:
                        checkpoint["processed"].append(url)

                    if url not in checkpoint["deleted"]:
                        checkpoint["deleted"].append(url)
            else:
                failed += 1
                checkpoint["failed"].append({
                    "url": url,
                    "time": datetime.now().isoformat(),
                })

            save_checkpoint(checkpoint)

        except CleanupExit:
            save_checkpoint(checkpoint)
            raise

        except Exception as error:
            failed += 1
            log(f"Error: {error}")

            checkpoint["failed"].append({
                "url": url,
                "error": str(error),
                "time": datetime.now().isoformat(),
            })
            save_checkpoint(checkpoint)

        if index < len(post_urls):
            human_delay(args)

    return ok, failed


# ============================================================
# RUN
# ============================================================

def run(args):
    mode = "delete" if args.execute else "dry_run"

    if args.execute and args.confirm != "DELETE":
        log('Mode hapus butuh argumen --confirm DELETE.')
        raise CleanupExit(EXIT_NOT_CONFIRMED, "not_confirmed", mode=mode)

    checkpoint = load_checkpoint()

    # Batas 1 jam hanya berlaku untuk penghapusan sungguhan.
    target = next_batch_time(checkpoint) if args.execute else None

    if target and datetime.now() < target:
        if not args.wait:
            log(f"Belum 1 jam. Batch berikutnya: {target.isoformat()}")
            raise CleanupExit(
                EXIT_TOO_EARLY,
                "too_early",
                mode=mode,
                next_batch_time=target.isoformat(),
            )
        wait_until(target)

    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            str(BROWSER_PROFILE),
            headless=args.headless and not args.login,
            channel="chromium",  # headless pakai Chromium penuh, bukan headless-shell
            viewport={"width": 1280, "height": 900},
        )

        page = context.pages[0] if context.pages else context.new_page()

        try:
            ensure_login(page, context, args)

            if args.login:
                log("Login tersimpan di browser profile.")
                return {"status": "login_saved", "mode": mode}

            require_security(page)

            # Ambil lebih banyak URL agar bisa melewati yang sudah diproses.
            post_urls = collect_post_urls(page, args, args.limit * 3)

            processed = set(checkpoint["processed"])
            pending = [u for u in post_urls if u not in processed][:args.limit]

            if not pending:
                log("Tidak ada postingan yang belum diproses.")
                return {"status": "nothing_pending", "mode": mode, "count": 0}

            log(f"Mode: {mode.upper()} — {len(pending)} postingan:")
            for index, url in enumerate(pending, start=1):
                log(f"  {index}. {url}")

            ok, failed = process_batch(page, checkpoint, pending, args)

            if args.execute:
                checkpoint["last_batch_time"] = datetime.now().isoformat()
                save_checkpoint(checkpoint)

            log("Batch selesai.")

            return {
                "status": "done",
                "mode": mode,
                "processed": ok,
                "failed": failed,
                "total_deleted": len(checkpoint["deleted"]),
            }

        finally:
            context.close()


# ============================================================
# ENTRY POINT
# ============================================================

def parse_args():
    parser = argparse.ArgumentParser(
        description="Hapus postingan Instagram secara bertahap (aman untuk bot)."
    )
    parser.add_argument("--username", default=DEFAULT_USERNAME,
                        help="Username Instagram (atau env INSTAGRAM_USERNAME)")
    parser.add_argument("--limit", type=int, default=DEFAULT_POSTS_PER_HOUR,
                        help="Maks postingan per batch (default 5)")
    parser.add_argument("--min-delay", type=float, default=DEFAULT_MIN_DELAY)
    parser.add_argument("--max-delay", type=float, default=DEFAULT_MAX_DELAY)
    parser.add_argument("--execute", action="store_true",
                        help="Hapus sungguhan (default: dry run)")
    parser.add_argument("--confirm", default="",
                        help='Wajib "DELETE" bersama --execute')
    parser.add_argument("--headless", action="store_true",
                        help="Jalankan browser tanpa jendela")
    parser.add_argument("--login", action="store_true",
                        help="Login manual sekali (membuka jendela browser)")
    parser.add_argument("--wait", action="store_true",
                        help="Tunggu sampai interval 1 jam terpenuhi, "
                             "bukan langsung keluar dengan kode 3")
    args = parser.parse_args()

    if not args.username:
        parser.error("--username wajib diisi (atau set env INSTAGRAM_USERNAME)")

    return args


def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass

    args = parse_args()
    result = {}
    code = EXIT_OK

    try:
        acquire_lock()
    except CleanupExit as exit_:
        print("RESULT: " + json.dumps({"status": exit_.status}), flush=True)
        return exit_.code

    try:
        result = run(args)

    except CleanupExit as exit_:
        code = exit_.code
        result = {"status": exit_.status, **exit_.extra}

    except KeyboardInterrupt:
        log("Program dihentikan.")
        code = EXIT_ERROR
        result = {"status": "interrupted"}

    except Exception as error:
        log(f"Fatal error: {error}")
        code = EXIT_ERROR
        result = {"status": "error", "error": str(error)}

    finally:
        release_lock()

    print("RESULT: " + json.dumps(result, ensure_ascii=False), flush=True)
    return code


if __name__ == "__main__":
    sys.exit(main())
