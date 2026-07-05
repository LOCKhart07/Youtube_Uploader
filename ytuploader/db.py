"""SQLite tracking DB.

The schema is kept byte-for-byte compatible with the original project's
`video_db_2.db` so existing upload history is preserved.
"""

from __future__ import annotations

import random
from contextlib import contextmanager

from sqlalchemy import (
    Boolean,
    Column,
    ForeignKey,
    Integer,
    String,
    and_,
    create_engine,
    func,
    or_,
)
from sqlalchemy.orm import declarative_base, sessionmaker

Base = declarative_base()


class AppFolder(Base):
    __tablename__ = "app_folders"
    app_folder_id = Column(Integer, primary_key=True)
    name = Column(String, unique=True)
    folder_path = Column(String)
    playlist_created = Column(Boolean)

    def __repr__(self) -> str:
        return f"AppFolder(id={self.app_folder_id}, name={self.name!r})"


class Video(Base):
    __tablename__ = "videos"
    video_id = Column(Integer, primary_key=True)
    app_folder_id = Column(Integer, ForeignKey("app_folders.app_folder_id"))
    video_name = Column(String)
    video_path = Column(String, unique=True)
    is_uploaded = Column(Boolean)
    youtube_id = Column(String)
    tags = Column(String)
    timestamp = Column(String)

    def __repr__(self) -> str:
        return f"Video(id={self.video_id}, name={self.video_name!r}, uploaded={self.is_uploaded})"


class Database:
    """Thin wrapper around a SQLAlchemy session factory."""

    def __init__(self, db_url: str):
        self.engine = create_engine(db_url)
        Base.metadata.create_all(self.engine)
        self._Session = sessionmaker(bind=self.engine)

    @contextmanager
    def session(self):
        s = self._Session()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    # ---- reads -------------------------------------------------------------

    def pending_videos(self, order: str = "fewest_remaining") -> list[tuple[Video, AppFolder]]:
        """Return (video, folder) pairs for everything not yet uploaded."""
        with self.session() as s:
            counts = (
                s.query(
                    AppFolder.app_folder_id,
                    func.count(Video.video_id).label("video_count"),
                )
                .join(Video, AppFolder.app_folder_id == Video.app_folder_id)
                .filter(Video.is_uploaded == False)  # noqa: E712
                .group_by(AppFolder.app_folder_id)
                .subquery()
            )

            q = (
                s.query(Video, AppFolder)
                .join(Video, AppFolder.app_folder_id == Video.app_folder_id)
                .join(counts, AppFolder.app_folder_id == counts.c.app_folder_id)
                .filter(Video.is_uploaded == False)  # noqa: E712
            )

            if order == "fewest_remaining":
                q = q.order_by(counts.c.video_count, AppFolder.app_folder_id)
            elif order == "name":
                q = q.order_by(AppFolder.name, Video.timestamp)

            rows = q.all()
            # Detach so the objects stay usable after the session closes.
            # (Folders repeat across rows, so expunge_all is simpler than a loop.)
            s.expunge_all()

        if order == "random":
            random.shuffle(rows)
        return rows

    def counts(self) -> tuple[int, int, int]:
        """(total, uploaded, remaining)."""
        with self.session() as s:
            uploaded = s.query(Video).filter_by(is_uploaded=True).count()
            remaining = s.query(Video).filter_by(is_uploaded=False).count()
        return uploaded + remaining, uploaded, remaining

    def irregular_videos(self) -> list[Video]:
        """Marked uploaded but missing a YouTube id - a sign of a half-failed run."""
        with self.session() as s:
            rows = (
                s.query(Video)
                .filter(
                    and_(
                        Video.is_uploaded == True,  # noqa: E712
                        or_(Video.youtube_id == None, Video.youtube_id == ""),  # noqa: E711
                    )
                )
                .all()
            )
            s.expunge_all()
        return rows

    def distribution(self) -> list[dict]:
        """Per-playlist uploaded / remaining / total counts, sorted by remaining."""
        with self.session() as s:
            def grouped(filt=None):
                q = (
                    s.query(AppFolder.name, func.count(Video.video_id))
                    .outerjoin(Video, Video.app_folder_id == AppFolder.app_folder_id)
                )
                if filt is not None:
                    q = q.filter(filt)
                return dict(q.group_by(AppFolder.name).all())

            uploaded = grouped(Video.is_uploaded == True)  # noqa: E712
            remaining = grouped(Video.is_uploaded == False)  # noqa: E712
            total = grouped()
            names = [n for (n,) in s.query(AppFolder.name).distinct().all()]

        rows = [
            {
                "playlist": n,
                "uploaded": uploaded.get(n, 0),
                "remaining": remaining.get(n, 0),
                "total": total.get(n, 0),
            }
            for n in names
        ]
        rows.sort(key=lambda d: d["remaining"])
        return rows

    # ---- writes ------------------------------------------------------------

    def get_or_create_folder(self, name: str, folder_path: str) -> int:
        with self.session() as s:
            folder = s.query(AppFolder).filter(AppFolder.name == name).first()
            if folder is None:
                folder = AppFolder(name=name, folder_path=folder_path, playlist_created=False)
                s.add(folder)
                s.flush()
            return folder.app_folder_id

    def mark_playlist_created(self, name: str) -> None:
        with self.session() as s:
            folder = s.query(AppFolder).filter(AppFolder.name == name).first()
            if folder is not None:
                folder.playlist_created = True

    def video_exists(self, video_path: str) -> bool:
        with self.session() as s:
            return s.query(Video).filter(Video.video_path == video_path).first() is not None

    def add_video(self, **kwargs) -> None:
        with self.session() as s:
            s.add(Video(**kwargs))

    def remove_orphaned_pending(self) -> int:
        """Delete pending rows whose file no longer exists (e.g. moved/renamed
        on disk). Uploaded rows are left alone - they're already on YouTube."""
        import os

        with self.session() as s:
            pending = s.query(Video).filter(Video.is_uploaded == False).all()  # noqa: E712
            orphans = [v for v in pending if not os.path.exists(v.video_path)]
            for v in orphans:
                s.delete(v)
            return len(orphans)

    def mark_uploaded(self, video_id: int, youtube_id: str) -> None:
        with self.session() as s:
            video = s.query(Video).filter_by(video_id=video_id).first()
            if video:
                video.is_uploaded = True
                video.youtube_id = youtube_id
