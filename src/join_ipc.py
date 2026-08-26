"""Single-instance IPC for the joiner.

Explorer invokes the command once per selected file. First invocation
becomes the server; subsequent invocations connect as clients, send their
path, and exit. Server debounces and emits `ready(list_of_paths)`.
"""
from __future__ import annotations

import os
import sys

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket


def _server_name(mode: str) -> str:
    user = os.environ.get("USERNAME") or os.environ.get("USER") or "user"
    return f"AutoSplitJoin_{user}_{mode}"


def try_forward_to_running(path: str, mode: str, timeout_ms: int = 400) -> bool:
    """Attempt to connect to an existing collector for this mode and send `path`.

    Returns True if the running instance accepted the path (we should exit).
    Returns False if no instance was running (we should become the server).
    """
    sock = QLocalSocket()
    sock.connectToServer(_server_name(mode))
    if not sock.waitForConnected(timeout_ms):
        return False
    payload = (path.replace("\r", "").replace("\n", "") + "\n").encode("utf-8")
    sock.write(payload)
    sock.waitForBytesWritten(timeout_ms)
    sock.disconnectFromServer()
    return True


class JoinCollector(QObject):
    """Collects paths sent by peer invocations, then emits `ready` after
    a debounce window elapses with no new input."""

    added = Signal(str)        # emitted immediately for each new unique path
    ready = Signal(list)       # emitted after debounce with the final ordered list

    def __init__(self, mode: str, debounce_ms: int = 500, parent=None):
        super().__init__(parent)
        self.mode = mode
        self._paths: list[str] = []
        self._server = QLocalServer(self)
        self._server.newConnection.connect(self._on_new_conn)
        # Clean up any stale socket left by a crashed prior instance.
        QLocalServer.removeServer(_server_name(mode))
        ok = self._server.listen(_server_name(mode))
        if not ok:
            print(f"JoinCollector: could not listen on {_server_name(mode)}: "
                  f"{self._server.errorString()}", file=sys.stderr)
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.timeout.connect(self._finalize)
        self._debounce_ms = int(debounce_ms)
        self._finalized = False

    def add_path(self, path: str) -> None:
        if not path:
            return
        path = os.path.abspath(path)
        if path in self._paths:
            self._kick()
            return
        self._paths.append(path)
        self.added.emit(path)
        self._kick()

    def paths(self) -> list[str]:
        return list(self._paths)

    def stop(self) -> None:
        self._server.close()

    def _kick(self) -> None:
        if self._finalized:
            return
        self._debounce.start(self._debounce_ms)

    def _on_new_conn(self) -> None:
        sock = self._server.nextPendingConnection()
        if sock is None:
            return
        if sock.waitForReadyRead(500):
            data = bytes(sock.readAll()).decode("utf-8", errors="replace")
            for line in data.splitlines():
                p = line.strip()
                if p:
                    self.add_path(p)
        sock.disconnectFromServer()

    def _finalize(self) -> None:
        if self._finalized:
            return
        self._finalized = True
        self.ready.emit(list(self._paths))
