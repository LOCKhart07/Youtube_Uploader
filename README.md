# ytuploader

Upload game-capture clips from a `Captures` folder to YouTube, one playlist per
game, without re-uploading anything you've already done.

Windows desktop app (tray icon, dashboard, scheduled uploads) plus a CLI.

## Quick start

1. Download `ytuploader-setup-<version>.exe` from the
   [latest release](../../releases/latest) and run it. It installs per-user, no
   admin needed. Windows SmartScreen may warn because the installer isn't
   code-signed: *More info → Run anyway*. Releases are built by GitHub Actions
   from this repository.
2. Open **Settings**, set your captures folder (one subfolder per game) and Save.
3. Open **Setup** and do the three steps: log in to YouTube in the app's own
   browser window, choose your OAuth client file (see below) and authorise the
   API.
4. Click **Scan + upload now**, or turn on automatic uploads in Settings.

**OAuth client:** playlists are managed through the YouTube Data API, so you
need your own *Desktop app* OAuth client: Google Cloud Console → enable the
*YouTube Data API v3* → APIs & Services → Credentials → Create OAuth client ID →
*Desktop app* → download the JSON. Without it clips still upload, they just
aren't added to playlists. If your Cloud project's consent screen is in
*Testing*, add your Google account as a test user; tokens then expire after 7
days, so publish the app (In production) to stop re-authorising every week.

## How it works

| Job | Tool | Why |
| --- | --- | --- |
| Upload the video file + title/description/tags | **Playwright** (browser) | No API quota; `videos.insert` would cost 1600 units (~6/day). |
| Create playlists & add the video to one | **YouTube Data API** | Cheap (50 units each → ~200/day, well above YouTube's daily upload ceiling). |
| Remember what's uploaded | **SQLite** | Nothing gets uploaded twice. |

Studio's playlist dropdown is flaky to automate, so it's skipped: the browser
does the upload and hands back the video id, then the API drops the video into
the right playlist. Far less to break.

## Running from source

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

copy config.example.toml config.toml      # then edit paths/settings
copy .env.example .env                     # only needed for email summaries
```

> **Browser:** this setup drives your installed **Google Chrome** via Playwright's
> `channel` option (`[upload].browser_channel` in `config.toml`), so there's no
> `playwright install` step. Playwright's own bundled browsers can fail to
> launch on Windows with a side-by-side (WinSxS) error; the system browser
> sidesteps that. Switch to `'msedge'` if you prefer Edge.

Edit `config.toml`, at minimum `captures_folder`.

Then authorise both halves once:

```bash
python -m ytuploader login     # opens a browser — log in to YouTube, press Enter
python -m ytuploader auth      # opens a browser — grant API access (playlists)
```

`login` saves YouTube cookies into a dedicated browser profile
(`playwright_profile/`). `auth` caches an API token in `token.json`. Both
persist, so you only do this once.

## Desktop app

`ytuploader.exe` is the same uploader with a window and a tray icon: a
dashboard of per-game progress, Scan / Upload / Stop buttons with a live log,
one-click Setup (YouTube login, OAuth client, API authorisation) and a
Settings form that writes `config.toml`. Closing the window keeps it in the
tray; quit from the tray menu. `--minimized` starts it straight in the tray.

### Installing

Build the installer (needs Inno Setup 6 once: `winget install JRSoftware.InnoSetup`):

```powershell
.\build_installer.ps1
```

This makes a one-folder PyInstaller build (starts instantly) and packages it as
`dist\ytuploader-setup-<version>.exe` (version from `ytuploader.__version__`).
The installer is per-user, needs no admin rights, and installs to
`%LOCALAPPDATA%\Programs\ytuploader` with a Start-menu shortcut, an optional
desktop shortcut and an optional **Start with Windows** entry. Uninstall from
Settings → Apps; it asks before deleting your data (default: keep it).

**Releases:** publishing a GitHub release runs `.github/workflows/release.yml`,
which builds `ytuploader-setup-<version>.exe` and the single-file
`ytuploader-<version>.exe` on a Windows runner and attaches both to the release.
The tag must match `ytuploader.__version__` (`1.2.0` or `v1.2.0`), so bump
`ytuploader/__init__.py` first. It can also be run by hand from the Actions tab,
which leaves the files as a workflow artifact.

### Where the data lives

- **Installed app:** `%LOCALAPPDATA%\ytuploader` — `config.toml`, the history DB
  (`uploads.db`), `token.json`, `client_secrets.json`, `.env` and
  `playwright_profile\`. File → *Open data folder* opens it.
- **Portable exe:** if a `config.toml` (or an empty file named `portable`) sits
  next to `ytuploader.exe`, it uses that folder instead. `.\build_exe.ps1`
  builds that single-file exe and copies it to the repo folder.
- **From source** (`python -m ytuploader ...`): the repo folder.
- `YTUPLOADER_HOME=<folder>` overrides all of the above.

On first start with an empty data folder, the installed app offers to **import**
an existing folder (the one with `config.toml`, e.g. this repo): it copies the
settings, history DB (renamed to `uploads.db`), tokens, `.env` and the browser
profile (minus Chrome's caches), and rewrites the paths. The source folder is
never changed, and an existing DB is never overwritten — a second import gets a
new `uploads-imported-<time>.db`. Run it again any time from File → *Import data
from another folder*, or from the CLI: `python -m ytuploader import <folder>`.

The installed and portable apps share one single-instance lock: if one is
running, launching the other just brings the running one to the front.

### Automatic uploads and reminders

Settings → *Automatic uploads and reminders* (the `[schedule]` section of
`config.toml`), both off by default:

- **Scan + upload** once a day at a set time, or every N hours. Same as clicking
  *Scan + upload now*; the tray shows "Uploaded N clips" when it finishes.
- **Reminder**: once a day the app scans the captures folder and, if at least N
  clips are waiting, shows a tray notification. It doesn't upload anything.

They only run while the app is open or in the tray, so turn on Start with
Windows. A daily time missed while the PC was off is caught up once, a couple of
minutes after the app next starts. Turning a rule on or changing it starts from
now: daily at 03:00 set at 10:00 first runs tomorrow at 03:00. A run that comes
due during another task waits until it finishes. The tray menu shows the next
auto-upload time; last-run times are kept in `state.json` in the data folder.

### Start with Windows

Settings → Windows → *Start with Windows* (applied on Save) adds or removes
`ytuploader.exe --minimized` under
`HKCU\Software\Microsoft\Windows\CurrentVersion\Run`. The installer's option
writes the same value; uninstalling removes it. Only the packaged exe can
register itself, not a source checkout.

Run it from source without building: `python -m ytuploader gui`.

## Daily use (CLI)

```bash
python -m ytuploader scan      # pick up new clips from the Captures folder
python -m ytuploader status    # see what's uploaded / remaining per playlist
python -m ytuploader upload    # upload everything pending (Ctrl+C stops cleanly)

python -m ytuploader run       # scan + upload in one shot
```

`upload` stops by itself when YouTube reports the daily upload limit, marks each
success in the DB immediately, and (optionally) emails a summary / shuts the PC
down when done — both off by default, see `config.toml`.

## Configuration

All settings live in `config.toml` (see `config.example.toml` for the full,
commented list). Highlights:

- `paths.captures_folder` — the folder with one subfolder per game.
- `paths.db_path` — the upload history DB (`uploads.db`).
- `upload.visibility` — `private` / `unlisted` / `public`.
- `upload.headless` — set `false` to watch the browser while debugging.
- `upload.max_uploads_per_run` — cap a session (0 = no cap).
- `notify.enabled` / `power.shutdown_when_done` — opt-in extras.

Secrets (the Gmail app password for email summaries) go in `.env`, never in
`config.toml` or source.

## Capture sources (NVIDIA and AMD)

The scanner understands both capture-filename conventions:

- **NVIDIA ShadowPlay** — `Apex Legends 2020.04.08 - 16.56.44.02.DVR.mp4`
- **AMD Adrenalin** — `Hollow Knight Silksong_replay_2025.09.14-01.35.mp4`
  (tidied to the title `Hollow Knight Silksong 2025.09.14-01.35`)

Manually-renamed clips with no timestamp (e.g. `4kclutch.mp4`) keep their name
and use the file's creation time as the recorded date.

AMD also drops cache/temp folders into the captures root (`replay_cache`,
`gif_replay_cache`, `InGame_replay_cache`, `thumb_cache`, plus `{GUID}`/`temp`
chunk folders). Those are skipped so they don't turn into junk playlists — see
`[scan].ignore_folders` in `config.toml`. AMD's `unknown` folder (clips it
couldn't attribute to a game) *is* scanned; add it to `ignore_folders` if you'd
rather triage those by hand first.

## When YouTube changes its UI

Browser automation breaks when Studio's layout changes. Every selector is in one
place — the `SEL` dict at the top of `ytuploader/uploader.py`. On any failure a
screenshot is saved to `debug_screenshots/` so you can see what the page looked
like and fix the selector.

## Layout

```
ytuploader/
  config.py        load config.toml + .env
  db.py            SQLAlchemy models + queries (schema unchanged)
  scanner.py       Captures folder -> DB
  youtube_api.py   playlists / playlistItems (cheap API)
  uploader.py      Playwright upload (the robust core)
  notify.py        optional email summary
  migrate.py       import an old data folder into the app's data folder
  autostart.py     Start with Windows (HKCU Run entry)
  schedule.py      when the tray app auto-uploads / reminds
  service.py       workflows shared by the CLI and the desktop app
  cli.py           command line orchestration
  gui/             PySide6 desktop app (tray, dashboard, settings)
packaging/
  ytuploader_app.py  PyInstaller entry point
  installer.iss      Inno Setup script
```

## License

MIT, see [LICENSE](LICENSE).
