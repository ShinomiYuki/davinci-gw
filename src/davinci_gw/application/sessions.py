"""管理进程内一次性 Prepared Session、TTL、容量和文件指纹。"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import RLock
from typing import Callable
from uuid import uuid4

from davinci_gw.application.generate import PreparedTransaction
from davinci_gw.contracts import (
    FileFingerprintDto,
    OperationStatus,
    PreviewResultDto,
    SessionState,
    UpdateRequestDto,
)


class InputFingerprintChangedError(OSError):
    """文件在一次指纹读取过程中变化，所得摘要不可作为稳定快照。"""


def fingerprint_file(path_value: str | Path) -> FileFingerprintDto:
    """流式计算文件指纹，避免为大 ARXML 分配等量内存。"""
    path = Path(path_value).expanduser().resolve()
    before = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise InputFingerprintChangedError(f"文件在计算指纹时发生变化：{path}")
    return FileFingerprintDto(str(path), after.st_size, after.st_mtime_ns, digest.hexdigest())


class SessionAccessError(LookupError):
    """携带稳定公共状态的内部会话访问失败。"""
    def __init__(self, status: OperationStatus, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


@dataclass(slots=True)
class PreparedSessionRecord:
    """仅存于应用进程内的会话记录；绝不进入公共 DTO。"""
    session_id: str
    request: UpdateRequestDto
    config_fingerprint: FileFingerprintDto
    baseline_fingerprint: FileFingerprintDto
    prepared: PreparedTransaction | None
    preview: PreviewResultDto
    created_at: str
    expires_at: str
    created_monotonic: float
    expires_monotonic: float
    state: SessionState = SessionState.READY


class PreparedSessionStore:
    """单锁保证状态转换原子性，重型写出在锁外执行。"""

    def __init__(
        self,
        *,
        ttl_seconds: float = 900.0,
        max_sessions: int = 8,
        monotonic: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], datetime] | None = None,
    ) -> None:
        """建立实例级有界存储，TTL 必须以单调时钟计算。"""
        if ttl_seconds <= 0 or max_sessions <= 0:
            raise ValueError("Prepared Session 的 TTL 和容量必须大于零。")
        self.ttl_seconds = ttl_seconds
        self.max_sessions = max_sessions
        self._monotonic = monotonic
        self._wall_clock = wall_clock or (lambda: datetime.now(timezone.utc))
        self._records: dict[str, PreparedSessionRecord] = {}
        self._lock = RLock()

    def create(
        self,
        request: UpdateRequestDto,
        config_fingerprint: FileFingerprintDto,
        baseline_fingerprint: FileFingerprintDto,
        prepared: PreparedTransaction,
        preview: PreviewResultDto,
    ) -> PreparedSessionRecord:
        """保存已完成事务；容量满时淘汰最早的非提交记录。"""
        with self._lock:
            self.cleanup_expired()
            while len(self._records) >= self.max_sessions:
                candidates = [item for item in self._records.values() if item.state is not SessionState.COMMITTING]
                if not candidates:
                    raise SessionAccessError(
                        OperationStatus.SESSION_INVALID, "SESSION_CAPACITY_FULL",
                        "Prepared Session 容量已满，且现有会话正在提交。",
                    )
                oldest = min(candidates, key=lambda item: item.created_monotonic)
                del self._records[oldest.session_id]
            now_mono = self._monotonic()
            now_wall = self._wall_clock()
            record = PreparedSessionRecord(
                session_id=str(uuid4()), request=request,
                config_fingerprint=config_fingerprint,
                baseline_fingerprint=baseline_fingerprint,
                prepared=prepared, preview=preview,
                created_at=now_wall.isoformat(),
                expires_at=(now_wall + timedelta(seconds=self.ttl_seconds)).isoformat(),
                created_monotonic=now_mono,
                expires_monotonic=now_mono + self.ttl_seconds,
            )
            self._records[record.session_id] = record
            return record

    def _lookup(self, session_id: str) -> PreparedSessionRecord:
        record = self._records.get(session_id)
        if record is None:
            raise SessionAccessError(
                OperationStatus.SESSION_MISSING, "SESSION_MISSING", "Prepared Session 不存在或已被容量淘汰。",
            )
        if record.state is SessionState.READY and self._monotonic() >= record.expires_monotonic:
            record.state = SessionState.EXPIRED
            record.prepared = None
        if record.state is SessionState.EXPIRED:
            raise SessionAccessError(OperationStatus.SESSION_EXPIRED, "SESSION_EXPIRED", "Prepared Session 已过期。")
        if record.state is SessionState.CONSUMED:
            raise SessionAccessError(OperationStatus.SESSION_CONSUMED, "SESSION_CONSUMED", "Prepared Session 已消费。")
        if record.state is not SessionState.READY:
            raise SessionAccessError(OperationStatus.SESSION_INVALID, "SESSION_INVALID", "Prepared Session 当前不可提交。")
        return record

    def acquire_for_commit(self, session_id: str) -> PreparedSessionRecord:
        """原子获取一次提交权，并把 READY 转为 COMMITTING。"""
        with self._lock:
            record = self._lookup(session_id)
            record.state = SessionState.COMMITTING
            return record

    def finish_commit(self, session_id: str, *, success: bool) -> None:
        """固定提交终态并立即释放内部大对象。"""
        with self._lock:
            record = self._records.get(session_id)
            if record is None:
                return
            record.state = SessionState.CONSUMED if success else SessionState.INVALID
            record.prepared = None

    def discard(self, session_id: str) -> None:
        """显式使 READY 会话失效并释放内部大对象。"""
        with self._lock:
            record = self._lookup(session_id)
            record.state = SessionState.INVALID
            record.prepared = None

    def cleanup_expired(self) -> int:
        """释放所有到期 READY 会话，保留轻量墓碑以区分过期。"""
        with self._lock:
            now = self._monotonic()
            expired = 0
            for record in self._records.values():
                if record.state is SessionState.READY and now >= record.expires_monotonic:
                    record.state = SessionState.EXPIRED
                    record.prepared = None
                    expired += 1
            return expired
