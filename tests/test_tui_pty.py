from __future__ import annotations

import fcntl
import os
from pathlib import Path
import pty
import select
import signal
import struct
import subprocess
import tempfile
import termios
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
FAKE_BACKEND = ROOT / "tests" / "fake-vm-helper"


def set_size(fd: int, rows: int, columns: int) -> None:
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, columns, 0, 0))


class PtyIntegrationTests(unittest.TestCase):
    def run_tui(self, term: str, rows: int, columns: int, fake_delay: float = 0,
                max_quit_latency: float | None = None, launch_action: bool = False,
                route_usb: bool = False) -> bytes:
        master, slave = pty.openpty()
        set_size(slave, rows, columns)
        before = termios.tcgetattr(slave)
        environment = os.environ.copy()
        environment.update(TERM=term, PYTHONPATH=str(ROOT))
        environment.pop("VM_HELPER_FAKE_DELAY", None)
        if fake_delay:
            environment["VM_HELPER_FAKE_DELAY"] = str(fake_delay)
        if route_usb:
            environment["VM_HELPER_FAKE_VM_STATE"] = "running"
        with tempfile.TemporaryDirectory() as runtime:
            environment["XDG_RUNTIME_DIR"] = runtime
            if route_usb:
                fake_bin = Path(runtime) / "bin"
                fake_bin.mkdir()
                os.symlink("/usr/bin/true", fake_bin / "sudo")
                environment["PATH"] = f"{fake_bin}:{environment['PATH']}"

            def child_setup() -> None:
                os.setsid()
                fcntl.ioctl(slave, termios.TIOCSCTTY, 0)

            process = subprocess.Popen(
                ["python3", "-m", "vm_helper_tui", "--backend", str(FAKE_BACKEND), "--mode", "dashboard"],
                cwd=ROOT,
                env=environment,
                stdin=slave,
                stdout=slave,
                stderr=slave,
                preexec_fn=child_setup,
                close_fds=True,
            )
            output = bytearray()
            deadline = time.monotonic() + 8
            try:
                time.sleep(0.7)
                if launch_action:
                    os.write(master, b"4")
                    time.sleep(0.1)
                    os.write(master, b"y")
                    time.sleep(0.8)
                    os.write(master, b"jk")
                    time.sleep(0.1)
                    os.write(master, b"\t\t\t")
                    time.sleep(0.2)
                elif route_usb:
                    os.write(master, b"5")
                    time.sleep(0.5)
                    os.write(master, b" ")
                    time.sleep(0.1)
                    os.write(master, b"a")
                    time.sleep(0.1)
                    os.write(master, b"y")
                    time.sleep(0.8)
                    os.write(master, b"q")
                    time.sleep(0.1)
                else:
                    os.write(master, b"\t")
                    time.sleep(0.2)
                    set_size(slave, max(18, rows - 3), max(70, columns - 5))
                    os.killpg(process.pid, signal.SIGWINCH)
                    time.sleep(0.2)
                    os.write(master, b"r")
                    time.sleep(0.3)
                quit_started = time.monotonic()
                os.write(master, b"q")
                while process.poll() is None and time.monotonic() < deadline:
                    readable, _, _ = select.select([master], [], [], 0.1)
                    if readable:
                        try:
                            output.extend(os.read(master, 65536))
                        except OSError:
                            break
                process.wait(timeout=2)
                if max_quit_latency is not None:
                    self.assertLess(time.monotonic() - quit_started, max_quit_latency)
                while True:
                    readable, _, _ = select.select([master], [], [], 0)
                    if not readable:
                        break
                    try:
                        output.extend(os.read(master, 65536))
                    except OSError:
                        break
                self.assertEqual(process.returncode, 0, output.decode("utf-8", "replace"))
                after = termios.tcgetattr(slave)
                self.assertEqual(after[3], before[3], "curses did not restore terminal local flags")
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=2)
                os.close(master)
                os.close(slave)
        return bytes(output)

    def test_linux_console_compact_and_xterm_large(self) -> None:
        cases = (("linux", 24, 80, "Overview"), ("xterm-256color", 40, 140, "Virtual Machine"))
        for term, rows, columns, layout_marker in cases:
            with self.subTest(term=term, size=(columns, rows)):
                output = self.run_tui(term, rows, columns)
                decoded = output.decode("utf-8", "replace")
                self.assertIn("VM HELPER", decoded)
                self.assertIn("LINUX", decoded)
                self.assertIn(layout_marker, decoded)

    def test_quit_remains_responsive_during_slow_telemetry(self) -> None:
        self.run_tui("xterm-256color", 24, 80, fake_delay=2.0, max_quit_latency=0.8)

    def test_action_stays_on_dashboard_and_streams_activity(self) -> None:
        output = self.run_tui("xterm-256color", 24, 80, launch_action=True)
        decoded = output.decode("utf-8", "replace")
        self.assertNotIn("ACTION TERMINAL", decoded)
        self.assertIn("Activity", decoded)
        self.assertIn("Configure", decoded)
        self.assertIn("Quit", decoded)
        self.assertIn("fake check started", decoded)
        self.assertIn("fake action complete", decoded)
        self.assertIn("Validate: SUCCEEDED", decoded)
        self.assertIn("Validate SUCCEEDED; see Activity Log for details", decoded)
        self.assertIn("PgUp/PgDn activity", decoded)

    def test_usb_page_stages_and_runs_live_routing(self) -> None:
        output = self.run_tui("xterm-256color", 40, 140, route_usb=True)
        decoded = output.decode("utf-8", "replace")
        self.assertIn("LIVE USB ROUTING", decoded)
        self.assertIn("Missing default", decoded)
        self.assertIn("fake usb-route started --windows 1234:abcd", decoded)
        self.assertIn("Activity Log", decoded)

    def test_classic_environment_fallback_exits_cleanly(self) -> None:
        master, slave = pty.openpty()
        set_size(slave, 24, 80)
        environment = os.environ.copy()
        environment.update(TERM="linux", VM_HELPER_TUI="0")
        process = subprocess.Popen(
            [str(ROOT / "vm-helper"), "menu"], cwd=ROOT, env=environment,
            stdin=slave, stdout=slave, stderr=slave, close_fds=True,
        )
        try:
            time.sleep(0.2)
            os.write(master, b"0")
            output = bytearray()
            deadline = time.monotonic() + 4
            while process.poll() is None and time.monotonic() < deadline:
                readable, _, _ = select.select([master], [], [], 0.1)
                if readable:
                    output.extend(os.read(master, 65536))
            process.wait(timeout=2)
            self.assertEqual(process.returncode, 0)
            self.assertIn("VM Helper", output.decode("utf-8", "replace"))
            self.assertIn("classic Bash menu", output.decode("utf-8", "replace"))
        finally:
            if process.poll() is None:
                process.terminate(); process.wait(timeout=2)
            os.close(master); os.close(slave)


if __name__ == "__main__":
    unittest.main()
