"""Curses application loop and action/wizard orchestration."""

from __future__ import annotations

import argparse
import curses
import locale
import os
from pathlib import Path
import subprocess
import time

from .protocol import ActionInfo, ActionStore, Backend, BackendError, PendingCall, records_by_kind
from .state import ACTIONS, COMPACT_TABS, DashboardState, WizardState, map_key, map_mouse
from .telemetry import HostCollector, VmDeltaTracker
from .views import Renderer


class Application:
    def __init__(self, screen: curses.window, backend_path: str, mode: str):
        self.screen = screen
        self.backend = Backend(backend_path)
        self.actions = ActionStore()
        self.host_collector = HostCollector()
        self.vm_tracker = VmDeltaTracker()
        self.state = DashboardState()
        self.renderer = Renderer(screen)
        self.wizard = WizardState() if mode == "configure" else None
        self.pending_action: int | None = None
        self.current_action: ActionInfo | None = None
        self.dynamic_call: PendingCall | None = None
        self.static_call: PendingCall | None = None
        self.action_marker: tuple[str, str, int | None] | None = None
        self.logs: list[str] = []
        self.running = True
        self.force_dynamic = True
        self.force_static = True
        screen.keypad(True)
        screen.timeout(100)
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        try:
            curses.mousemask(curses.ALL_MOUSE_EVENTS | curses.REPORT_MOUSE_POSITION)
            curses.mouseinterval(0)
        except curses.error:
            pass

    def _finish_dynamic(self) -> None:
        if self.dynamic_call is None:
            return
        try:
            records = self.dynamic_call.poll()
            if records is None:
                return
            self.dynamic_call = None
            host = self.host_collector.sample()
            vm_rates = self.vm_tracker.update(records)
            self.state.apply_snapshot(records, host, vm_rates)
        except BackendError as exc:
            self.dynamic_call = None
            self.state.error = str(exc)
            self.state.last_dynamic = time.monotonic()

    def _finish_static(self) -> None:
        if self.static_call is None:
            return
        try:
            records = self.static_call.poll()
            if records is None:
                return
            self.static_call = None
            self.state.inventory.update(records)
            self.state.last_static = time.monotonic()
            if self.wizard and self.wizard.vm and not self.wizard.usb_ids:
                self.wizard.usb_ids = {
                    f"{item[1]}:{item[2]}" for item in self.state.inventory.usb if item[3] == "yes"
                }
        except BackendError as exc:
            self.static_call = None
            self.state.error = str(exc)
            self.state.last_static = time.monotonic()

    def refresh_telemetry(self) -> None:
        self._finish_dynamic()
        self._finish_static()
        now = time.monotonic()
        if self.dynamic_call is None and (self.force_dynamic or now - self.state.last_dynamic >= 1.0):
            try:
                self.dynamic_call = self.backend.begin_snapshot()
            except BackendError as exc:
                self.state.error = str(exc)
                self.state.last_dynamic = now
            self.force_dynamic = False
        if self.static_call is None and (self.force_static or now - self.state.last_static >= 5.0):
            vm = self.wizard.vm if self.wizard and self.wizard.vm else ""
            try:
                self.static_call = self.backend.begin_inventory(vm)
            except BackendError as exc:
                self.state.error = str(exc)
                self.state.last_static = now
            self.force_static = False

    @staticmethod
    def _action_label(action: str) -> str:
        return next((spec.label for spec in ACTIONS if spec.worker_action == action), action)

    def _action_notice(self, action: ActionInfo) -> str:
        label = self._action_label(action.action)
        if action.active:
            return f"{label} is RUNNING (PID {action.pid}); Action Log updates live"
        return f"{label} {action.status_text}; see Action Log for details"

    def refresh_action(self) -> None:
        self.current_action = self.actions.current()
        self.logs = self.actions.read_log(self.current_action)
        self.state.log_scroll = min(self.state.log_scroll, max(0, len(self.logs) - 1))
        if self.current_action is None:
            self.action_marker = None
            return
        marker = (
            self.current_action.token,
            self.current_action.effective_status,
            self.current_action.returncode,
        )
        previous = self.action_marker
        if previous is None or previous[0] != marker[0]:
            self.state.notice = self._action_notice(self.current_action)
        elif previous[1] == "running" and marker[1] != "running":
            self.state.notice = self._action_notice(self.current_action)
        self.action_marker = marker

    def loop(self) -> None:
        try:
            while self.running:
                self.refresh_telemetry()
                self.refresh_action()
                modal = ACTIONS[self.pending_action].confirmation if self.pending_action is not None else ""
                self.renderer.render(
                    self.state,
                    self.current_action,
                    self.logs,
                    self.wizard,
                    modal,
                    self.state.terminal_log,
                )
                key = self.screen.getch()
                if key == -1:
                    continue
                if self.pending_action is not None:
                    if key in (ord("y"), ord("Y")):
                        self.confirm_pending()
                    elif key in (ord("n"), ord("N"), 27, ord("q")):
                        self.pending_action = None
                    continue
                command = self._command_for_key(key)
                if command:
                    self.handle(command)
        finally:
            for call in (self.dynamic_call, self.static_call):
                if call is not None:
                    call.cancel()

    def _command_for_key(self, key: int) -> str | None:
        if key != curses.KEY_MOUSE:
            return map_key(key)
        try:
            _, x, y, _, button_state = curses.getmouse()
        except curses.error:
            return None
        return map_mouse(y, x, button_state, self.renderer.regions)

    def handle(self, command: str) -> None:
        if self.wizard is not None:
            self._handle_wizard(command)
            return
        if self.state.terminal_log:
            self._handle_terminal_log(command)
            return
        if command in ("quit", "escape"):
            self.running = False
        elif command == "up":
            self.state.move_action(-1)
        elif command == "down":
            self.state.move_action(1)
        elif command == "next_tab":
            self.state.move_tab(1)
        elif command == "previous_tab":
            self.state.move_tab(-1)
        elif command.startswith("tab:"):
            self.state.tab = int(command.split(":", 1)[1])
        elif command == "activate":
            self._request_action(self.state.selected_action)
        elif command.startswith("action:"):
            index = int(command.split(":", 1)[1])
            self.state.selected_action = index
            self._request_action(index)
        elif command == "refresh":
            self.force_dynamic = self.force_static = True
            self.state.notice = "Refreshing telemetry and hardware inventory"
        elif command == "configure":
            if self.current_action and self.current_action.active:
                self.state.notice = "Configuration is disabled while an action is active"
            else:
                self.wizard = WizardState()
                self.force_static = True
        elif command == "terminal_log":
            self.state.terminal_log = True
        elif command == "log_up":
            self.state.log_scroll = min(max(0, len(self.logs) - 1), self.state.log_scroll + 5)
        elif command == "log_down":
            self.state.log_scroll = max(0, self.state.log_scroll - 5)
        elif command == "resize":
            self.screen.erase()

    def _handle_terminal_log(self, command: str) -> None:
        if command == "quit":
            self.running = False
        elif command in ("escape", "terminal_log"):
            self.state.terminal_log = False
        elif command in ("log_up", "up"):
            amount = 1 if command == "up" else 5
            self.state.log_scroll = min(max(0, len(self.logs) - 1), self.state.log_scroll + amount)
        elif command in ("log_down", "down"):
            amount = 1 if command == "down" else 5
            self.state.log_scroll = max(0, self.state.log_scroll - amount)
        elif command == "resize":
            self.screen.erase()

    def _request_action(self, index: int) -> None:
        if not 0 <= index < len(ACTIONS):
            return
        if self.current_action and self.current_action.active:
            self.state.notice = "An action is active; conflicting actions are disabled"
            return
        self.pending_action = index

    def _authorize_sudo(self) -> bool:
        if os.geteuid() == 0:
            return True
        try:
            curses.def_prog_mode()
            curses.endwin()
            print("vm-helper needs reusable sudo authorization before the detached transition.", flush=True)
            result = subprocess.run(["sudo", "-v"], check=False)
            return result.returncode == 0
        except OSError as exc:
            self.state.error = f"sudo authorization failed: {exc}"
            return False
        finally:
            try:
                curses.reset_prog_mode()
                self.screen.refresh()
            except curses.error:
                pass

    def confirm_pending(self) -> None:
        if self.pending_action is None:
            return
        spec = ACTIONS[self.pending_action]
        self.pending_action = None
        if spec.mutating and not self._authorize_sudo():
            self.state.error = "sudo authorization was cancelled; no action was started"
            return
        try:
            records = self.backend.start_worker(spec.worker_action)
            worker = records_by_kind(records).get("worker", [()])[0]
            self.state.notice = f"Detached {spec.label} action started (PID {worker[1] if len(worker) > 1 else '?'})"
            self.state.error = ""
            self.state.log_scroll = 0
            self.state.tab = COMPACT_TABS.index("Logs")
            self.state.terminal_log = True
            if len(worker) > 1:
                self.action_marker = (worker[0], "running", None)
            self.refresh_action()
            self.force_dynamic = True
        except BackendError as exc:
            self.state.error = str(exc)

    def _handle_wizard(self, command: str) -> None:
        assert self.wizard is not None
        if command == "quit":
            self.wizard = None
            return
        if command in ("escape", "previous_tab"):
            if self.wizard.back():
                self.wizard = None
            return
        if command == "up":
            self.wizard.move(-1, self.state.inventory)
        elif command == "down":
            self.wizard.move(1, self.state.inventory)
        elif command == "toggle":
            self.wizard.toggle(self.state.inventory)
        elif command == "refresh":
            self.force_static = True
        elif command == "activate" or command == "next_tab":
            previous = self.wizard.step
            if self.wizard.advance(self.state.inventory):
                self._write_configuration()
            elif previous == 0 and self.wizard.step == 1:
                self.force_static = True

    def _write_configuration(self) -> None:
        assert self.wizard is not None
        try:
            self.backend.configure(self.wizard.vm, self.wizard.gpu, sorted(self.wizard.usb_ids))
            self.state.notice = "Configuration written with a timestamped backup"
            self.state.error = ""
            self.wizard = None
            self.force_dynamic = self.force_static = True
        except BackendError as exc:
            self.wizard.message = str(exc)


def run_curses(screen: curses.window, backend: str, mode: str) -> None:
    Application(screen, backend, mode).loop()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="vm-helper curses dashboard")
    parser.add_argument("--backend", required=True)
    parser.add_argument("--mode", choices=("dashboard", "configure"), default="dashboard")
    args = parser.parse_args(argv)
    locale.setlocale(locale.LC_ALL, "")
    backend = str(Path(args.backend).resolve())
    try:
        curses.wrapper(run_curses, backend, args.mode)
    except KeyboardInterrupt:
        return 130
    return 0
