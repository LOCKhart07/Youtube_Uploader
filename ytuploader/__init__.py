"""ytuploader - upload game capture clips to YouTube, organised into playlists.

A modern rewrite of the old selenium_youtube hybrid:
  * the actual upload runs through Playwright (robust, no API quota),
  * playlists are managed via the cheap YouTube Data API,
  * progress is tracked in a local SQLite DB so nothing uploads twice.
"""

__version__ = "1.0.0"
