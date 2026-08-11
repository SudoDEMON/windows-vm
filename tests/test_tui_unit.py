from __future__ import annotations

import curses
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from vm_helper_tui.protocol import ActionInfo, ActionStore, Backend, ProtocolError, parse_records, records_by_kind
from vm_helper_tui.state import (
    DEFAULT_LAYOUT_PROFILE,
    Inventory,
    MouseRegion,
    PANEL_REGISTRY,
    WizardState,
    layout_mode,
    map_key,
    map_mouse,
)
from vm_helper_tui.telemetry import VmDeltaTracker, counter_rate, derive_mode, sparkline


def payload(*records: tuple[str, tuple[object, ...]]) -> bytes:
    result = bytearray(b"VMH1\0")
    for kind, fields in records:
        result.extend(kind.encode() + b"\0" + str(len(fields)).encode() + b"\0")
        for field in fields:
            result.extend(str(field).encode() + b"\0")
    return bytes(result)


class ProtocolTests(unittest.TestCase):
    def test_nul_records_preserve_labels_and_paths(self) -> None:
        data = payload(("usb", ("Label | with spaces\nand newline", "1234", "abcd")),
                       ("disk", ("/dev/disk/by-id/a path",)))
        records = parse_records(data)
        self.assertEqual(records[0].fields[0], "Label | with spaces\nand newline")
        self.assertEqual(records[1].fields[0], "/dev/disk/by-id/a path")

    def test_rejects_bad_header_and_truncated_record(self) -> None:
        with self.assertRaises(ProtocolError):
            parse_records(b"bad")
        with self.assertRaises(ProtocolError):
            parse_records(b"VMH1\0vm\0" + b"2\0only-one\0")


class TelemetryTests(unittest.TestCase):
    def test_counter_rate_handles_first_sample_and_reset(self) -> None:
        self.assertEqual(counter_rate(10, None, 1), 0)
        self.assertEqual(counter_rate(5, 10, 1), 0)
        self.assertEqual(counter_rate(30, 10, 2), 10)

    def test_vm_delta_rates(self) -> None:
        ticks = iter((1.0, 3.0))
        tracker = VmDeltaTracker(clock=lambda: next(ticks))
        first = parse_records(payload(("vm", ("vm", "running", "uri", 2, 1_000_000_000, 0, 0)),
                                             ("vm_net", ("tap0", 100, 200)),
                                             ("vm_block", ("vda", "/dev/x", 300, 500))))
        second = parse_records(payload(("vm", ("vm", "running", "uri", 2, 2_000_000_000, 0, 0)),
                                              ("vm_net", ("tap0", 300, 600)),
                                              ("vm_block", ("vda", "/dev/x", 700, 1300))))
        tracker.update(first)
        rates = tracker.update(second)
        self.assertAlmostEqual(rates.cpu, 50.0)
        self.assertEqual(rates.net_rx, 100.0)
        self.assertEqual(rates.block_write, 400.0)

    def test_ascii_sparkline_and_modes(self) -> None:
        graph = sparkline([0, 50, 100], 3, 100)
        self.assertEqual(len(graph), 3)
        self.assertTrue(graph.isascii())
        self.assertEqual(derive_mode("shut off", "active", ["nvidia"]), "LINUX")
        self.assertEqual(derive_mode("running", "inactive", ["vfio-pci"]), "WINDOWS")
        self.assertEqual(derive_mode("running", "active", ["vfio-pci"]), "LINUX + WINDOWS")
        self.assertEqual(derive_mode("shut off", "inactive", ["nvidia"]), "LINUX TTY")
        self.assertIn("ERROR", derive_mode("running", "active", ["nvidia"]))


class LayoutAndInputTests(unittest.TestCase):
    def test_responsive_thresholds(self) -> None:
        self.assertEqual(layout_mode(140, 40), "large")
        self.assertEqual(layout_mode(80, 24), "compact")
        self.assertEqual(layout_mode(79, 24), "small")
        self.assertEqual(layout_mode(120, 34), "compact")
        registered = {panel.key for panel in PANEL_REGISTRY}
        profiled = {key for column in DEFAULT_LAYOUT_PROFILE["large_columns"] for key in column}
        self.assertEqual(profiled, registered)

    def test_key_and_mouse_mapping(self) -> None:
        self.assertEqual(map_key(curses.KEY_DOWN), "down")
        self.assertEqual(map_key(ord("1")), "action:0")
        self.assertEqual(map_key(27), "escape")
        region = MouseRegion(2, 3, 4, 8, "action:2")
        self.assertEqual(map_mouse(3, 5, curses.BUTTON1_CLICKED, [region]), "action:2")
        self.assertIsNone(map_mouse(8, 8, curses.BUTTON1_CLICKED, [region]))


class WizardTests(unittest.TestCase):
    def inventory(self) -> Inventory:
        inv = Inventory()
        inv.update(parse_records(payload(
            ("domain", ("win", "shut off")),
            ("gpu", ("0000:01:00.0", "nvidia", 13, "GPU", "no", "0x10de")),
            ("companion", ("0000:01:00.0", "0000:01:00.1", "snd", 13, "Audio", "no")),
            ("usb_inventory", ("Keyboard", "1234", "abcd", "no")),
            ("disk", ("/dev/raw", "/dev/sda", "yes", "live", "")),
        )))
        return inv

    def test_wizard_selection_toggle_and_warnings(self) -> None:
        inv = self.inventory(); wizard = WizardState()
        self.assertFalse(wizard.advance(inv)); self.assertEqual(wizard.vm, "win")
        self.assertFalse(wizard.advance(inv)); self.assertEqual(wizard.gpu, "0000:01:00.0")
        wizard.advance(inv)
        wizard.toggle(inv)
        self.assertIn("1234:abcd", wizard.usb_ids)
        warnings = wizard.persistence_warnings(inv)
        self.assertEqual(len(warnings), 2)
        wizard.advance(inv); wizard.advance(inv)
        self.assertTrue(wizard.advance(inv))


class ReconnectionTests(unittest.TestCase):
    def test_action_status_text_distinguishes_outcomes(self) -> None:
        path = Path("/tmp/action.log")
        failed = ActionInfo("failed", 0, "check", "failed", 1, 2, 1, path, path)
        complete = ActionInfo("complete", 0, "check", "complete", 1, 2, 0, path, path)
        stale = ActionInfo("stale", 999_999_999, "check", "running", 1, None, None, path, path)
        self.assertEqual(failed.status_text, "FAILED (exit 1)")
        self.assertEqual(complete.status_text, "SUCCEEDED")
        self.assertEqual(stale.status_text, "STOPPED (incomplete metadata)")

    def test_snapshot_call_is_nonblocking(self) -> None:
        fake = Path(__file__).with_name("fake-vm-helper")
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {"XDG_RUNTIME_DIR": directory, "VM_HELPER_FAKE_DELAY": "0.3"},
        ):
            call = Backend(str(fake), timeout=2).begin_snapshot()
            try:
                self.assertIsNone(call.poll())
                deadline = time.monotonic() + 2
                records = None
                while records is None and time.monotonic() < deadline:
                    time.sleep(0.02)
                    records = call.poll()
                self.assertIsNotNone(records)
                assert records is not None
                self.assertIn("snapshot", records_by_kind(records))
            finally:
                call.cancel()

    def test_reads_active_metadata_and_log(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); log = root / "action-token.log"; meta = root / "action-token.meta"
            log.write_text("one\ntwo\n")
            meta.write_bytes(payload(("action", ("token", os.getpid(), "windows", "running", 1, "", "", log))))
            action = ActionStore(root).current()
            self.assertIsNotNone(action)
            assert action is not None
            self.assertTrue(action.active)
            self.assertEqual(ActionStore.read_log(action), ["one", "two"])

    def test_detached_fake_action_survives_launcher_and_reconnects(self) -> None:
        fake = Path(__file__).with_name("fake-vm-helper")
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"XDG_RUNTIME_DIR": directory}):
            records = Backend(str(fake)).start_worker("windows")
            worker = records_by_kind(records)["worker"][0]
            store = ActionStore(Path(directory) / "vm-helper")
            action = store.current()
            self.assertIsNotNone(action)
            assert action is not None
            self.assertEqual(action.token, worker[0])
            self.assertTrue(action.active)
            time.sleep(0.7)
            action = store.current()
            assert action is not None
            self.assertEqual(action.status, "complete")
            self.assertEqual(action.returncode, 0)
            self.assertIn("fake action complete", "\n".join(store.read_log(action)))
            self.assertEqual(action.metadata_path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(action.log_path.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
