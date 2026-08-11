"""Binary backend protocol and detached action reconnection."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time
from typing import Iterable


MAGIC = b"VMH1\0"


class ProtocolError(ValueError):
    pass


class BackendError(RuntimeError):
    pass


@dataclass(frozen=True)
class Record:
    kind: str
    fields: tuple[str, ...]


def parse_records(payload: bytes) -> list[Record]:
    if not payload.startswith(MAGIC):
        raise ProtocolError("missing VMH1 protocol header")
    parts = payload[len(MAGIC) :].split(b"\0")
    if parts and parts[-1] == b"":
        parts.pop()
    records: list[Record] = []
    index = 0
    while index < len(parts):
        if index + 1 >= len(parts):
            raise ProtocolError("truncated record header")
        kind = parts[index].decode("utf-8", "surrogateescape")
        try:
            count = int(parts[index + 1])
        except ValueError as exc:
            raise ProtocolError(f"invalid field count for {kind!r}") from exc
        if count < 0 or count > 4096:
            raise ProtocolError(f"unreasonable field count for {kind!r}: {count}")
        start = index + 2
        end = start + count
        if end > len(parts):
            raise ProtocolError(f"truncated {kind!r} record")
        fields = tuple(value.decode("utf-8", "surrogateescape") for value in parts[start:end])
        records.append(Record(kind, fields))
        index = end
    return records


def records_by_kind(records: Iterable[Record]) -> dict[str, list[tuple[str, ...]]]:
    grouped: dict[str, list[tuple[str, ...]]] = {}
    for record in records:
        grouped.setdefault(record.kind, []).append(record.fields)
    return grouped


def _decode_backend_result(returncode: int, stdout: bytes, stderr: bytes) -> list[Record]:
    if returncode != 0:
        message = stderr.decode("utf-8", "replace").strip()
        raise BackendError(message or f"backend exited with status {returncode}")
    try:
        return parse_records(stdout)
    except ProtocolError as exc:
        detail = stderr.decode("utf-8", "replace").strip()
        raise BackendError(f"{exc}{': ' + detail if detail else ''}") from exc


class PendingCall:
    """A backend call that can be polled without blocking the curses input loop."""

    def __init__(self, command: list[str], timeout: float):
        self.command = command
        self.timeout = timeout
        self.deadline = time.monotonic() + timeout
        self._stdout = tempfile.TemporaryFile()
        self._stderr = tempfile.TemporaryFile()
        self._closed = False
        try:
            self.process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=self._stdout,
                stderr=self._stderr,
                start_new_session=True,
            )
        except OSError as exc:
            self.close()
            raise BackendError(str(exc)) from exc

    def poll(self) -> list[Record] | None:
        returncode = self.process.poll()
        if returncode is None:
            if time.monotonic() < self.deadline:
                return None
            self.cancel()
            raise BackendError(f"backend timed out after {self.timeout:g} seconds")
        self.process.wait()
        self._stdout.seek(0)
        self._stderr.seek(0)
        stdout = self._stdout.read()
        stderr = self._stderr.read()
        self.close()
        return _decode_backend_result(returncode, stdout, stderr)

    def cancel(self) -> None:
        if self.process.poll() is None:
            try:
                os.killpg(self.process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                self.process.wait(timeout=0.1)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                self.process.wait(timeout=0.5)
        self.close()

    def close(self) -> None:
        if not self._closed:
            self._stdout.close()
            self._stderr.close()
            self._closed = True


class Backend:
    def __init__(self, executable: str, timeout: float = 4.0):
        self.executable = str(Path(executable).resolve())
        self.timeout = timeout

    def _call(self, arguments: list[str], timeout: float | None = None) -> list[Record]:
        try:
            result = subprocess.run(
                [self.executable, *arguments],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout or self.timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise BackendError(str(exc)) from exc
        return _decode_backend_result(result.returncode, result.stdout, result.stderr)

    def snapshot(self) -> list[Record]:
        return self._call(["machine", "snapshot"])

    def begin_snapshot(self) -> PendingCall:
        return PendingCall([self.executable, "machine", "snapshot"], self.timeout)

    def inventory(self, vm: str = "") -> list[Record]:
        args = ["machine", "inventory"]
        if vm:
            args.append(vm)
        return self._call(args)

    def begin_inventory(self, vm: str = "") -> PendingCall:
        args = [self.executable, "machine", "inventory"]
        if vm:
            args.append(vm)
        return PendingCall(args, self.timeout)

    def configure(self, vm: str, gpu: str, usb_ids: Iterable[str]) -> list[Record]:
        args = ["machine", "configure", "--vm", vm, "--gpu", gpu]
        for usb_id in usb_ids:
            args.extend(("--usb", usb_id))
        return self._call(args, timeout=15.0)

    def start_worker(self, action: str) -> list[Record]:
        return self._call(["machine", "worker-start", action])


def default_runtime_dir() -> Path:
    base = os.environ.get("XDG_RUNTIME_DIR")
    run_user = Path("/run/user") / str(os.getuid())
    if not base and run_user.is_dir():
        base = str(run_user)
    if not base:
        base = str(Path(tempfile.gettempdir()) / f"vm-helper-{os.getuid()}")
    return Path(base) / "vm-helper"


@dataclass(frozen=True)
class ActionInfo:
    token: str
    pid: int
    action: str
    status: str
    started: int
    finished: int | None
    returncode: int | None
    log_path: Path
    metadata_path: Path

    @property
    def active(self) -> bool:
        return self.status == "running" and process_alive(self.pid)

    @property
    def effective_status(self) -> str:
        if self.status == "running" and not self.active:
            return "stopped"
        return self.status

    @property
    def status_text(self) -> str:
        status = self.effective_status
        if status == "running":
            return "RUNNING"
        if status == "complete" and self.returncode in (None, 0):
            return "SUCCEEDED"
        if status == "failed" or self.returncode not in (None, 0):
            suffix = f" (exit {self.returncode})" if self.returncode is not None else ""
            return f"FAILED{suffix}"
        if status == "stopped":
            return "STOPPED (incomplete metadata)"
        return status.upper()


def process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class ActionStore:
    def __init__(self, runtime_dir: Path | None = None):
        self.runtime_dir = runtime_dir or default_runtime_dir()

    def _read_metadata(self, path: Path) -> ActionInfo | None:
        try:
            records = parse_records(path.read_bytes())
        except (OSError, ProtocolError):
            return None
        action = next((record for record in records if record.kind == "action"), None)
        if action is None or len(action.fields) != 8:
            return None
        token, pid, name, status, started, finished, returncode, log_path = action.fields
        try:
            return ActionInfo(
                token=token,
                pid=int(pid),
                action=name,
                status=status,
                started=int(started),
                finished=int(finished) if finished else None,
                returncode=int(returncode) if returncode else None,
                log_path=Path(log_path),
                metadata_path=path,
            )
        except ValueError:
            return None

    def actions(self) -> list[ActionInfo]:
        if not self.runtime_dir.is_dir():
            return []
        actions = filter(None, (self._read_metadata(path) for path in self.runtime_dir.glob("action-*.meta")))
        return sorted(actions, key=lambda item: (item.started, item.token), reverse=True)

    def current(self) -> ActionInfo | None:
        actions = self.actions()
        active = next((action for action in actions if action.active), None)
        return active or (actions[0] if actions else None)

    @staticmethod
    def read_log(action: ActionInfo | None, max_bytes: int = 256_000) -> list[str]:
        if action is None:
            return []
        try:
            with action.log_path.open("rb") as handle:
                handle.seek(0, os.SEEK_END)
                size = handle.tell()
                handle.seek(max(0, size - max_bytes))
                data = handle.read()
        except OSError:
            return []
        return data.decode("utf-8", "replace").splitlines()
