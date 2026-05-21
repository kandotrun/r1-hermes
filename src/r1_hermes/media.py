from __future__ import annotations

import os
import secrets
import stat
import time
from dataclasses import dataclass
from pathlib import Path

from .payloads import UNSUPPORTED_MEDIA_CODE, UNSUPPORTED_MEDIA_MESSAGE, ImageAttachment

DEFAULT_MEDIA_MAX_BYTES = 10 * 1024 * 1024
DEFAULT_MEDIA_TTL_SECONDS = 15 * 60
UPLOAD_DIR_NAME = "uploads"

MEDIA_TOO_LARGE_CODE = "MEDIA_TOO_LARGE"
MEDIA_TOO_LARGE_MESSAGE = "media file exceeds limit"

_EXTENSION_TO_MIME = {
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}
_MIME_TO_EXTENSION = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}


class MediaUploadError(ValueError):
    """Safe media validation error for untrusted Rabbit R1 attachments."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class StoredMediaFile:
    path: Path
    mime_type: str
    size_bytes: int


class MediaUploadStore:
    """Private upload store for authenticated media passed to Hermes as MEDIA files."""

    def __init__(
        self,
        state_dir: Path,
        *,
        max_file_bytes: int = DEFAULT_MEDIA_MAX_BYTES,
        ttl_seconds: int = DEFAULT_MEDIA_TTL_SECONDS,
    ):
        self.state_dir = state_dir
        self.upload_dir = state_dir / UPLOAD_DIR_NAME
        self.max_file_bytes = max(1, int(max_file_bytes))
        self.ttl_seconds = max(1, int(ttl_seconds))
        self._ensure_private_dirs()

    def store_all(
        self,
        attachments: tuple[ImageAttachment, ...],
    ) -> tuple[StoredMediaFile, ...]:
        if not attachments:
            return ()
        self.prune_expired()
        stored: list[StoredMediaFile] = []
        try:
            for attachment in attachments:
                stored.append(self.store(attachment))
        except Exception:
            self.remove(*(item.path for item in stored))
            raise
        return tuple(stored)

    def store(self, attachment: ImageAttachment) -> StoredMediaFile:
        if attachment.data is None:
            raise MediaUploadError(UNSUPPORTED_MEDIA_CODE, UNSUPPORTED_MEDIA_MESSAGE)
        raw = attachment.data
        declared_mime = _normalize_mime_type(attachment.mime_type)
        if len(raw) > self.max_file_bytes:
            raise MediaUploadError(MEDIA_TOO_LARGE_CODE, MEDIA_TOO_LARGE_MESSAGE)

        sniffed_mime = _sniff_image_mime(raw)
        if sniffed_mime is None:
            raise MediaUploadError(UNSUPPORTED_MEDIA_CODE, UNSUPPORTED_MEDIA_MESSAGE)
        if declared_mime is not None and declared_mime != sniffed_mime:
            raise MediaUploadError(UNSUPPORTED_MEDIA_CODE, UNSUPPORTED_MEDIA_MESSAGE)

        requested_extension = _safe_extension(attachment.filename)
        if (
            requested_extension is not None
            and _EXTENSION_TO_MIME[requested_extension] != sniffed_mime
        ):
            raise MediaUploadError(UNSUPPORTED_MEDIA_CODE, UNSUPPORTED_MEDIA_MESSAGE)
        extension = requested_extension or _MIME_TO_EXTENSION[sniffed_mime]
        path = self._new_upload_path(extension)

        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        fd = os.open(path, flags, stat.S_IRUSR | stat.S_IWUSR)
        try:
            with os.fdopen(fd, "wb") as handle:
                fd = -1
                handle.write(raw)
        except Exception:
            try:
                path.unlink()
            except OSError:
                pass
            raise
        finally:
            if fd != -1:  # pragma: no cover - defensive cleanup
                os.close(fd)
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        return StoredMediaFile(path=path.resolve(), mime_type=sniffed_mime, size_bytes=len(raw))

    def remove(self, *paths: Path) -> None:
        for path in paths:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

    def prune_expired(self) -> int:
        self._ensure_private_dirs()
        now = time.time()
        removed = 0
        for entry in self.upload_dir.iterdir():
            try:
                metadata = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            if not stat.S_ISREG(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode):
                continue
            if now - metadata.st_mtime <= self.ttl_seconds:
                continue
            try:
                entry.unlink()
            except OSError:
                continue
            removed += 1
        return removed

    def _ensure_private_dirs(self) -> None:
        if self.upload_dir.is_symlink():
            raise ValueError("media upload directory must not be a symlink")
        self.state_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.state_dir, stat.S_IRWXU)
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.upload_dir, stat.S_IRWXU)

    def _new_upload_path(self, extension: str) -> Path:
        for _ in range(10):
            name = f"r1-media-{int(time.time() * 1000)}-{secrets.token_urlsafe(16)}{extension}"
            path = self.upload_dir / name
            if not path.exists():
                return path
        raise MediaUploadError("MEDIA_STORE_UNAVAILABLE", "media upload store unavailable")


def _normalize_mime_type(value: str | None) -> str | None:
    if value is None:
        return None
    media_type = value.split(";", 1)[0].strip().lower()
    if not media_type:
        return None
    if media_type == "image/jpg":
        media_type = "image/jpeg"
    if media_type not in _MIME_TO_EXTENSION:
        raise MediaUploadError(UNSUPPORTED_MEDIA_CODE, UNSUPPORTED_MEDIA_MESSAGE)
    return media_type


def _safe_extension(filename: str | None) -> str | None:
    if filename is None:
        return None
    suffix = Path(filename).suffix.lower()
    if not suffix:
        return None
    if suffix not in _EXTENSION_TO_MIME:
        raise MediaUploadError(UNSUPPORTED_MEDIA_CODE, UNSUPPORTED_MEDIA_MESSAGE)
    return suffix


def _sniff_image_mime(raw: bytes) -> str | None:
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if raw.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(raw) >= 12 and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    return None
