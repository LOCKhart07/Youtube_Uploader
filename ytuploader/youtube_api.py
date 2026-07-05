"""YouTube Data API client - used ONLY for cheap playlist operations.

Quota costs (daily budget is 10,000 units by default):
    playlists.list        ~1 unit
    playlists.insert       50 units
    playlistItems.insert   50 units

The expensive `videos.insert` (1600 units) is deliberately NOT used here - that
job is done by the browser uploader, which costs no quota. At 50 units per
playlist-add we can place ~200 videos/day, comfortably above YouTube's own
per-day upload ceiling.
"""

from __future__ import annotations

from pathlib import Path

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

SCOPES = ["https://www.googleapis.com/auth/youtube"]


class QuotaExceeded(RuntimeError):
    """Raised when the API rejects a call because the daily quota is spent."""


def _is_quota_error(exc: HttpError) -> bool:
    reason = ""
    try:
        for err in exc.error_details or []:
            reason = err.get("reason", "")
            if reason in ("quotaExceeded", "dailyLimitExceeded"):
                return True
    except Exception:
        pass
    return getattr(exc, "status_code", None) == 403 and "quota" in str(exc).lower()


class YouTubeAPI:
    def __init__(self, client_secrets_file: Path, token_file: Path):
        self._client_secrets = Path(client_secrets_file)
        self._token_file = Path(token_file)
        self._service = build("youtube", "v3", credentials=self._credentials())

    def _credentials(self) -> Credentials:
        creds: Credentials | None = None
        if self._token_file.exists():
            creds = Credentials.from_authorized_user_file(str(self._token_file), SCOPES)

        if creds and creds.valid:
            return creds

        refreshed = False
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                refreshed = True
            except RefreshError:
                # Refresh token expired/revoked server-side (e.g. an OAuth
                # consent screen still in "Testing" mode expires these after
                # 7 days). Fall through and re-run the interactive flow.
                print("Cached YouTube token is no longer valid - re-authorizing...")

        if not refreshed:
            if not self._client_secrets.exists():
                raise FileNotFoundError(
                    f"OAuth client secrets not found at {self._client_secrets}. "
                    "Download them from Google Cloud Console (YouTube Data API)."
                )
            self._require_desktop_client()
            flow = InstalledAppFlow.from_client_secrets_file(str(self._client_secrets), SCOPES)
            creds = flow.run_local_server(port=0)

        self._token_file.write_text(creds.to_json(), encoding="utf-8")
        return creds

    def _require_desktop_client(self) -> None:
        """A 'web' OAuth client causes redirect_uri_mismatch with the loopback
        flow. Fail early with actionable guidance instead of a cryptic 400."""
        import json

        data = json.loads(self._client_secrets.read_text(encoding="utf-8"))
        if "installed" in data:
            return
        raise RuntimeError(
            f"{self._client_secrets.name} is a '{next(iter(data), 'web')}'-type OAuth "
            "client, which can't use the local sign-in flow (Error 400: "
            "redirect_uri_mismatch).\n"
            "Fix: Google Cloud Console -> APIs & Services -> Credentials -> "
            "Create credentials -> OAuth client ID -> Application type: 'Desktop app'.\n"
            "Download its JSON and overwrite client_secrets.json, then re-run "
            "`python -m ytuploader auth`."
        )

    # ---- playlists ---------------------------------------------------------

    def list_playlists(self) -> dict[str, str]:
        """Return {playlist_title: playlist_id} for all of the user's playlists."""
        playlists: dict[str, str] = {}
        page_token = None
        while True:
            try:
                resp = (
                    self._service.playlists()
                    .list(part="snippet", mine=True, maxResults=50, pageToken=page_token)
                    .execute()
                )
            except HttpError as e:
                if _is_quota_error(e):
                    raise QuotaExceeded("playlists.list hit the daily quota") from e
                raise
            for item in resp.get("items", []):
                playlists[item["snippet"]["title"]] = item["id"]
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return playlists

    def create_playlist(self, title: str, description: str = "", privacy: str = "private") -> str:
        try:
            resp = (
                self._service.playlists()
                .insert(
                    part="snippet,status",
                    body={
                        "snippet": {"title": title, "description": description or title},
                        "status": {"privacyStatus": privacy},
                    },
                )
                .execute()
            )
        except HttpError as e:
            if _is_quota_error(e):
                raise QuotaExceeded("playlists.insert hit the daily quota") from e
            raise
        return resp["id"]

    def add_to_playlist(self, video_id: str, playlist_id: str) -> None:
        try:
            self._service.playlistItems().insert(
                part="snippet",
                body={
                    "snippet": {
                        "playlistId": playlist_id,
                        "resourceId": {"kind": "youtube#video", "videoId": video_id},
                    }
                },
            ).execute()
        except HttpError as e:
            if _is_quota_error(e):
                raise QuotaExceeded("playlistItems.insert hit the daily quota") from e
            raise
