"""Web 会话：内存态 session + 临时照片目录。

隐私取舍（对齐 PRD「照片仅本次分析使用，不持久化」）：
  - 上传的照片写进系统临时目录（tempfile.mkdtemp），**不落进仓库、不入库**；
  - session 空闲超过 TTL（默认 30 分钟）自动回收，连带删除照片目录；
  - 进程退出时删掉整个临时根目录。

会话只存内存：CLI 之外的单机 demo 够用，不需要数据库。
"""

from __future__ import annotations

import atexit
import logging
import shutil
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

LOG = logging.getLogger("beauty_mirror.web.sessions")

DEFAULT_TTL_SECONDS = 30 * 60


@dataclass
class Session:
    sid: str
    image_path: Path
    image_name: str = ""
    image_mime: str = "image/jpeg"
    image_width: int = 0
    image_height: int = 0
    created_at: float = field(default_factory=time.time)
    touched_at: float = field(default_factory=time.time)
    questionnaire: dict[str, Any] = field(default_factory=dict)
    answers: dict[str, str] = field(default_factory=dict)
    vision: dict[str, Any] = field(default_factory=dict)
    skin: dict[str, Any] = field(default_factory=dict)
    plan: dict[str, Any] = field(default_factory=dict)
    compliance: dict[str, Any] = field(default_factory=dict)
    report: dict[str, Any] | None = None
    style_tag: str = ""
    cards: dict[str, dict[str, Any]] = field(default_factory=dict)
    beautified: dict[str, dict[str, Any]] = field(default_factory=dict)
    vision_provider: str = ""

    def touch(self) -> None:
        self.touched_at = time.time()


class SessionStore:
    """线程安全的会话表 + 临时目录管理。"""

    def __init__(self, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> None:
        self.ttl_seconds = int(ttl_seconds)
        self._root = Path(tempfile.mkdtemp(prefix="beauty_mirror_web_"))
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()
        self._sweep_stale_roots()
        atexit.register(self.close)  # 即使只 import 未启服务，退出时也能回收
        LOG.info("临时目录: %s", self._root)

    @property
    def root(self) -> Path:
        return self._root

    def _sweep_stale_roots(self) -> None:
        """启动时清理其他进程遗留的陈旧临时根（如被 kill -9 的实例）。

        只删“比自己旧得多”的根，不动可能仍在运行的实例（其根 mtime 较新）。
        """
        cutoff = time.time() - max(self.ttl_seconds, 3600)
        try:
            for path in self._root.parent.glob("beauty_mirror_web_*"):
                if path == self._root or not path.is_dir():
                    continue
                try:
                    if path.stat().st_mtime < cutoff:
                        shutil.rmtree(path, ignore_errors=True)
                        LOG.info("清理陈旧临时目录: %s", path.name)
                except OSError:
                    continue
        except Exception as exc:  # noqa: BLE001 - 清理失败不影响启动
            LOG.debug("清理陈旧临时目录失败: %s", exc)

    # --- 生命周期 ---------------------------------------------------------
    def create(self, sid: str, image_bytes: bytes, image_name: str, mime: str) -> Session:
        with self._lock:
            self._purge_locked()
            folder = self._root / sid
            folder.mkdir(parents=True, exist_ok=True)
            suffix = Path(image_name or "").suffix.lower() or _suffix_for_mime(mime)
            image_path = folder / f"original{suffix}"
            image_path.write_bytes(image_bytes)
            session = Session(
                sid=sid,
                image_path=image_path,
                image_name=image_name or image_path.name,
                image_mime=mime or "image/jpeg",
            )
            self._sessions[sid] = session
            return session

    def get(self, sid: str) -> Session | None:
        if not sid:
            return None
        with self._lock:
            session = self._sessions.get(sid)
            if session is not None:
                session.touch()
            return session

    def drop(self, sid: str) -> None:
        with self._lock:
            session = self._sessions.pop(sid, None)
        if session is not None:
            shutil.rmtree(session.image_path.parent, ignore_errors=True)

    def purge_expired(self) -> int:
        with self._lock:
            return self._purge_locked()

    def close(self) -> None:
        with self._lock:
            self._sessions.clear()
        shutil.rmtree(self._root, ignore_errors=True)
    # --- 内部 -------------------------------------------------------------
    def _purge_locked(self) -> int:
        now = time.time()
        stale = [
            sid for sid, session in self._sessions.items()
            if now - session.touched_at > self.ttl_seconds
        ]
        for sid in stale:
            session = self._sessions.pop(sid, None)
            if session is not None:
                shutil.rmtree(session.image_path.parent, ignore_errors=True)
        return len(stale)


def _suffix_for_mime(mime: str) -> str:
    return {
        "image/png": ".png",
        "image/webp": ".webp",
        "image/gif": ".gif",
        "image/bmp": ".bmp",
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
    }.get((mime or "").lower(), ".jpg")
