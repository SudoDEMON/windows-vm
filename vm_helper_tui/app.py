"""Curses application loop and action/wizard orchestration."""

from __future__ import annotations

import argparse
import curses
import locale
import os
from pathlib import Path
import subprocess
import time

from .protocol import ActionInfo, ActionStore, Backend, BackendError, records_by_kind
from .state import ACTIONS, DashboardState, WizardState, map_key, map_mouse
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
        self.logs: list[str] = []
        self.running = True
        self.force_dynamic = True
        self.force_static = True
        screen.keypad(True)
        screen.timeout(200)
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        try:
            curses.mousemask(curses.ALL_MOUSE_EVENTS | curses.REPORT_MOUSE_POSITION)
            curses.mouseinterval(0)
        except curses.error:
            pass

    def refresh_dynamic(self) -> None:
        host = self.host_collector.sample()
        try:
            records = self.backend.snapshot()
            vm_rates = self.vm_tracker.update(records)
            self.state.apply_snapshot(records, host, vm_rates)
        except BackendError as exc:
            self.state.host = host
            self.state.error = str(exc)
            self.state.last_dynamic = time.monotonic()

    def refresh_static(self) -> None:
        vm = self.wizard.vm if self.wizard and self.wizard.vm else ""
        try:
            self.state.inventory.update(self.backend.inventory(vm))
            self.state.last_static = time.monotonic()
            if self.wizard and self.wizard.vm and not self.wizard.usb_ids:
                self.wizard.usb_ids = {
                    f"{item[1]}:{item[2]}" for item in self.state.inventory.usb if item[3] == "yes"
                }
        except BackendError as exc:
            self.state.error = str(exc)
            self.state.last_static = time.monotonic()

    def refresh_action(self) -> None:
        self.current_action = self.actions.current()
        self.logs = self.actions.read_log(self.current_action)
        self.state.log_scroll = min(self.state.log_scroll, max(0, len(self.logs) - 1))

    def loop(self) -> None:
        while self.running:
            now = time.monotonic()
            if self.force_dynamic or now - self.state.last_dynamic >= 1.0:
                self.refresh_dynamic()
                self.force_dynamic = False
            if self.force_static or now - self.state.last_static >= 5.0:
                self.refresh_static()
                self.force_static = False
            self.refresh_action()
            modal = ACTIONS[self.pending_action].confirmation if self.pending_action is not None else ""
            self.renderer.render(self.state, self.current_action, self.logs, self.wizard, modal)
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
        elif command == "log_up":
            self.state.log_scroll = min(max(0, len(self.logs) - 1), self.state.log_scroll + 5)
        elif command == "log_down":
            self.state.log_scroll = max(0, self.state.log_scroll - 5)
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
