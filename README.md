# ytuploader

Upload game-capture clips from a `Captures` folder to YouTube, one playlist per
game, without re-uploading anything you've already done.

This is a modernised rewrite of the original project. The painful parts —
the YouTube API's 1600-unit-per-upload quota, the abandoned `selenium_youtube`
library, hardcoded paths and a Gmail password sitting in source — are gone.

## How it works

| Job | Tool | Why |
| --- | --- | --- |
| Upload the video file + title/description/tags | **Playwright** (browser) | No API quota; `videos.insert` would cost 1600 units (~6/day). |
| Create playlists & add the video to one | **YouTube Data API** | Cheap (50 units each → ~200/day, well above YouTube's daily upload ceiling). |
| Remember what's uploaded | **SQLite** | Nothing gets uploaded twice. Your existing history is reused as-is. |

The old code clicked through Studio's flaky playlist dropdown. We skip that:
the browser does the upload and hands back the video id, then the API drops the
video into the right playlist. Far less to break.

## One-time setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

copy config.example.toml config.toml      # then edit paths/settings
copy .env.example .env                     # only needed for email summaries
```

> **Browser:** this setup drives your installed **Google Chrome** via Playwright's
> `channel` option (`[upload].browser_channel` in `config.toml`), so there's no
> `playwright install` step. Playwright's own bundled browsers fail to launch on
> this machine with a Windows side-by-side (WinSxS) error; the system browser
> sidesteps that. Switch to `'msedge'` if you prefer Edge.

Edit `config.toml` — at minimum `captures_folder` and `db_path`.

Then authorise both halves once:

```bash
python -m ytuploader login     # opens a browser — log in to YouTube, press Enter
python -m ytuploader auth      # opens a browser — grant API access (playlists)
```

`login` saves YouTube cookies into a dedicated browser profile
(`playwright_profile/`). `auth` caches an API token in `token.json`. Both
persist, so you only do this once.

## Daily use

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

- `paths.captures_folder` — your `D:\Videos\Captures` root.
- `paths.db_path` — defaults to `final/video_db_2.db`, your existing history.
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

## ⚠️ Security note

The old `final/main.py` / `final/multi.py` had a Gmail **app password committed
in plain text**. Revoke it at <https://myaccount.google.com/apppasswords> and
generate a fresh one for `.env` if you want email summaries.

## Layout

```
ytuploader/
  config.py        load config.toml + .env
  db.py            SQLAlchemy models + queries (schema unchanged)
  scanner.py       Captures folder -> DB
  youtube_api.py   playlists / playlistItems (cheap API)
  uploader.py      Playwright upload (the robust core)
  notify.py        optional email summary
  cli.py           command line orchestration
```

The original scripts (`final/`, `main.py`, `selenium_wala.py`, …) are left in
place for reference but are no longer used.
