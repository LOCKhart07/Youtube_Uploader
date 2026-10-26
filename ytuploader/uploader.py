"""Browser-driven YouTube upload via Playwright.

This is the robust replacement for the old `selenium_youtube` library. It drives
youtube.com/upload in a persistent browser profile (so you stay logged in across
runs) and returns the uploaded video id. Playlist assignment is intentionally NOT
done here - that brittle dropdown is handled afterwards through the API.

All DOM selectors live in one block (`SEL`) so that when YouTube Studio changes
its UI you only edit one place. On any failure a screenshot is saved under
debug_screenshots/ to make selector breakage easy to diagnose.
"""

from __future__ import annotations

import enum
import time
from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import (
    Error as PlaywrightError,
    TimeoutError as PlaywrightTimeout,
    sync_playwright,
)

from .config import PROJECT_ROOT

UPLOAD_URL = "https://www.youtube.com/upload"
DEBUG_DIR = PROJECT_ROOT / "debug_screenshots"

# --- Centralised selectors. Update HERE when Studio's UI changes. -----------
SEL = {
    "file_input": "input[type='file']",
    "title": "#title-textarea #textbox",
    "description": "#description-textarea #textbox",
    "not_for_kids": "tp-yt-paper-radio-button[name='VIDEO_MADE_FOR_KIDS_NOT_MFK']",
    "show_more": "#toggle-button",
    "tags_input": "#tags-container #text-input",
    "next_button": "#next-button",
    "visibility_radio": "tp-yt-paper-radio-button[name='{vis}']",  # PUBLIC/PRIVATE/UNLISTED
    "video_link": "a[href^='https://youtu.be/']",
    "done_button": "#done-button",
    "dialog": "ytcp-uploads-dialog",
    "error_text": ".error-area, .error-short, ytcp-uploads-dialog",
    "progress_label": "span.progress-label, .progress-label, ytcp-video-upload-progress",
}

# Once the byte transfer is done, Studio's status flips away from "Uploading X%"
# to one of these. Processing/checks then continue server-side - safe to close.
_UPLOAD_DONE_PHRASES = (
    "upload complete",
    "processing",
    "checks complete",
    "finished processing",
)

_DAILY_LIMIT_PHRASES = (
    "daily upload limit",
    "you've reached the limit",
    "try again later",
)


class Status(enum.Enum):
    SUCCESS = "success"
    DAILY_LIMIT = "daily_limit"
    NOT_LOGGED_IN = "not_logged_in"
    FAILED = "failed"


@dataclass
class UploadResult:
    status: Status
    video_id: str | None = None
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.status is Status.SUCCESS


class UploadSession:
    """Opens one persistent browser context and uploads videos one at a time.

    Use as a context manager:
        with UploadSession(profile_dir, headless=True) as session:
            result = session.upload(path, title, description, tags, "private")
    """

    def __init__(
        self,
        profile_dir: Path,
        headless: bool = True,
        channel: str = "chrome",
        timeout_ms: int = 90_000,
    ):
        self.profile_dir = Path(profile_dir)
        self.headless = headless
        # Drive a system-installed Chromium browser ("chrome"/"msedge") rather
        # than Playwright's bundled build, which fails to launch on some Windows
        # setups (side-by-side / WinSxS errors). Set to None to use the bundle.
        self.channel = channel or None
        self.timeout_ms = timeout_ms
        self._pw = None
        self._context = None
        self._page = None

    def __enter__(self) -> "UploadSession":
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self._pw = sync_playwright().start()
        # Strip the automation fingerprint so Google's "this browser may not be
        # secure" sign-in block doesn't trigger: drop the --enable-automation
        # switch, hide navigator.webdriver, and keep the normal Chrome sandbox.
        self._context = self._pw.chromium.launch_persistent_context(
            str(self.profile_dir),
            headless=self.headless,
            channel=self.channel,
            chromium_sandbox=True,
            ignore_default_args=["--enable-automation"],
            args=["--disable-blink-features=AutomationControlled"],
        )
        self._context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )
        self._context.set_default_timeout(self.timeout_ms)
        self._page = self._context.pages[0] if self._context.pages else self._context.new_page()
        return self

    def __exit__(self, *_exc) -> None:
        # The user may have closed the window manually (common during `login`);
        # closing an already-closed context raises, so swallow it. The persistent
        # profile is written to disk as you browse, so the session is safe either way.
        try:
            if self._context:
                self._context.close()
        except PlaywrightError:
            pass
        finally:
            if self._pw:
                try:
                    self._pw.stop()
                except Exception:
                    pass

    # ------------------------------------------------------------------ helpers

    def _save_screenshot(self, name: str) -> Path | None:
        try:
            DEBUG_DIR.mkdir(exist_ok=True)
            path = DEBUG_DIR / f"{name}.png"
            self._page.screenshot(path=str(path), full_page=True)
            return path
        except Exception:
            return None

    def _looks_like_daily_limit(self) -> bool:
        try:
            text = (self._page.inner_text(SEL["dialog"], timeout=2_000) or "").lower()
        except PlaywrightError:
            text = (self._page.content() or "").lower()
        return any(p in text for p in _DAILY_LIMIT_PHRASES)

    def _fill_contenteditable(self, selector: str, text: str) -> None:
        box = self._page.locator(selector)
        box.click()
        box.fill("")          # works on contenteditable; clears any placeholder text
        box.fill(text)

    def _dismiss_popups(self) -> None:
        """Close Studio's occasional feature-announcement / upsell modals that
        overlay the upload dialog (e.g. "You're all set ... Got it")."""
        labels = ("Got it", "No thanks", "Not now", "Dismiss", "Skip", "Maybe later")
        for label in labels:
            try:
                loc = self._page.locator(
                    f"ytcp-button:has-text('{label}'), "
                    f"tp-yt-paper-button:has-text('{label}'), "
                    f"button:has-text('{label}')"
                )
                for i in range(loc.count()):
                    el = loc.nth(i)
                    if el.is_visible():
                        el.click(timeout=2_000)
            except PlaywrightError:
                pass

    # ------------------------------------------------------------------- upload

    def upload(
        self,
        video_path: str | Path,
        title: str,
        description: str,
        tags: list[str],
        visibility: str = "private",
    ) -> UploadResult:
        video_path = Path(video_path)
        if not video_path.exists():
            return UploadResult(Status.FAILED, message=f"file not found: {video_path}")

        page = self._page
        try:
            page.goto(UPLOAD_URL, wait_until="domcontentloaded")

            # If YouTube bounced us to a sign-in page, the profile isn't logged in.
            if "accounts.google.com" in page.url or "signin" in page.url:
                return UploadResult(Status.NOT_LOGGED_IN, message="profile is not logged in")

            # Studio sometimes pops a feature-announcement modal over the dialog.
            self._dismiss_popups()

            # Selecting the file kicks off the (quota-free) upload.
            file_input = page.locator(SEL["file_input"]).first
            file_input.wait_for(state="attached")
            file_input.set_input_files(str(video_path))

            # YouTube sometimes shows the limit dialog instead of the details form.
            try:
                page.locator(SEL["title"]).wait_for(timeout=30_000)
            except PlaywrightTimeout:
                if self._looks_like_daily_limit():
                    return UploadResult(Status.DAILY_LIMIT, message="daily upload limit reached")
                # A late popup may be covering the form - dismiss and retry once.
                self._dismiss_popups()
                page.locator(SEL["title"]).wait_for(timeout=20_000)

            # --- Details page -------------------------------------------------
            # Title may be pre-filled from the filename; replace it explicitly.
            time.sleep(1)  # let Studio finish auto-populating the title
            self._fill_contenteditable(SEL["title"], title[:100])
            if description:
                self._fill_contenteditable(SEL["description"], description[:4900])

            # "Not made for kids"
            try:
                page.locator(SEL["not_for_kids"]).click(timeout=10_000)
            except PlaywrightError:
                pass

            # Tags live behind the "Show more" expander.
            if tags:
                try:
                    page.locator(SEL["show_more"]).click(timeout=10_000)
                    self._page.locator(SEL["tags_input"]).fill(",".join(tags)[:480])
                except PlaywrightError:
                    pass  # tags are nice-to-have, never fail the upload over them

            # Early best-effort id read (may be empty this soon).
            video_id = self._read_video_id()

            # Details -> Monetisation -> Checks -> Visibility (3 Next clicks).
            for _ in range(3):
                page.locator(SEL["next_button"]).click()
                time.sleep(0.5)

            # --- Visibility ---------------------------------------------------
            page.locator(SEL["visibility_radio"].format(vis=visibility.upper())).click()

            # The share link is reliably present on the Visibility page - read it
            # HERE, before Done. After Done the final "processing" dialog has no
            # link, which previously caused false "no id" failures + re-uploads.
            if not video_id:
                video_id = self._read_video_id()

            if not video_id:
                shot = self._save_screenshot(f"no_id_visibility_{video_path.stem}")
                return UploadResult(
                    Status.FAILED,
                    message=f"could not read video id on visibility page (see {shot})",
                )

            # --- Publish ------------------------------------------------------
            # Wait for the bytes to finish so closing the browser can't abort it.
            self._wait_for_upload_complete()
            page.locator(SEL["done_button"]).click()

            if self._looks_like_daily_limit():
                return UploadResult(Status.DAILY_LIMIT, message="daily upload limit reached")

            # Close YouTube's final "Upload complete / processing" dialog if shown.
            try:
                page.locator("ytcp-button:has-text('Close'), button:has-text('Close')").first.click(
                    timeout=5_000
                )
            except PlaywrightError:
                pass

            # Give YouTube a moment to register the new video before the API
            # tries to add it to a playlist.
            time.sleep(2)
            return UploadResult(Status.SUCCESS, video_id=video_id)

        except PlaywrightTimeout as e:
            if self._looks_like_daily_limit():
                return UploadResult(Status.DAILY_LIMIT, message="daily upload limit reached")
            shot = self._save_screenshot(f"timeout_{video_path.stem}")
            return UploadResult(Status.FAILED, message=f"timeout: {e} (see {shot})")
        except PlaywrightError as e:
            shot = self._save_screenshot(f"error_{video_path.stem}")
            return UploadResult(Status.FAILED, message=f"{e} (see {shot})")

    def _read_video_id(self) -> str | None:
        try:
            href = self._page.locator(SEL["video_link"]).first.get_attribute(
                "href", timeout=10_000
            )
        except PlaywrightError:
            return None
        if href and "youtu.be/" in href:
            return href.rsplit("/", 1)[-1].split("?")[0]
        return None

    def _wait_for_upload_complete(self, timeout_s: int = 2700) -> bool:
        """Wait only until the file bytes finish transferring, then return so the
        caller can publish and move on. Processing/checks run server-side and
        survive the browser closing - we do NOT wait for them.

        Critical for large files: closing the browser while Studio still shows
        "Uploading X%" aborts the upload, so we block while that's showing and
        proceed the moment it flips to "Processing"/"Upload complete".
        """
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            try:
                label = self._page.locator(SEL["progress_label"]).first.inner_text(
                    timeout=5_000
                ).lower()
            except PlaywrightError:
                label = ""
            if "uploading" in label:
                pass  # bytes still transferring - keep waiting
            elif any(p in label for p in _UPLOAD_DONE_PHRASES):
                return True
            time.sleep(2)
        return False
