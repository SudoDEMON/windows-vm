"""Responsive, ASCII-border curses views."""

from __future__ import annotations

from dataclasses import dataclass
import curses
from typing import Iterable

from .protocol import ActionInfo
from .state import ACTIONS, COMPACT_TABS, DashboardState, MouseRegion, WizardState, WIZARD_STEPS, layout_mode
from .telemetry import safe_int, sparkline


@dataclass(frozen=True)
class Rect:
    y: int
    x: int
    h: int
    w: int


def bytes_text(value: float) -> str:
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    size = float(value)
    for unit in units:
        if abs(size) < 1024 or unit == units[-1]:
            return f"{size:5.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TiB"


def duration_text(seconds: float) -> str:
    value = max(0, int(seconds))
    days, value = divmod(value, 86400)
    hours, value = divmod(value, 3600)
    minutes, _ = divmod(value, 60)
    return f"{days}d {hours:02d}:{minutes:02d}" if days else f"{hours:02d}:{minutes:02d}"


class Renderer:
    def __init__(self, screen: curses.window):
        self.screen = screen
        self.regions: list[MouseRegion] = []
        self.colors = self._colors()

    @staticmethod
    def _colors() -> dict[str, int]:
        colors = {name: 0 for name in ("title", "good", "warn", "bad", "dim", "select")}
        if not curses.has_colors():
            colors["select"] = curses.A_REVERSE
            return colors
        try:
            curses.start_color()
            curses.use_default_colors()
            palette = (
                (39, 82, 214, 196, 244) if getattr(curses, "COLORS", 0) >= 256
                else (curses.COLOR_CYAN, curses.COLOR_GREEN, curses.COLOR_YELLOW, curses.COLOR_RED, curses.COLOR_BLUE)
            )
            for index, color in enumerate(palette, 1):
                curses.init_pair(index, color, -1)
            colors.update(title=curses.color_pair(1) | curses.A_BOLD, good=curses.color_pair(2),
                          warn=curses.color_pair(3), bad=curses.color_pair(4) | curses.A_BOLD,
                          dim=curses.color_pair(5), select=curses.color_pair(1) | curses.A_REVERSE)
        except curses.error:
            colors["select"] = curses.A_REVERSE
        return colors

    def text(self, y: int, x: int, value: object, width: int | None = None, attr: int = 0) -> None:
        height, screen_width = self.screen.getmaxyx()
        if y < 0 or y >= height or x < 0 or x >= screen_width:
            return
        rendered = str(value).replace("\0", "?").replace("\n", " ")
        limit = max(0, screen_width - x)
        if width is not None:
            limit = min(limit, max(0, width))
        try:
            self.screen.addnstr(y, x, rendered, limit, attr)
        except curses.error:
            pass

    def box(self, rect: Rect, title: str) -> Rect:
        if rect.h < 3 or rect.w < 4:
            return Rect(rect.y, rect.x, 0, 0)
        y2, x2 = rect.y + rect.h - 1, rect.x + rect.w - 1
        self.text(rect.y, rect.x, "+" + "-" * (rect.w - 2) + "+", rect.w)
        self.text(y2, rect.x, "+" + "-" * (rect.w - 2) + "+", rect.w)
        for y in range(rect.y + 1, y2):
            self.text(y, rect.x, "|")
            self.text(y, x2, "|")
        self.text(rect.y, rect.x + 2, f" {title} ", rect.w - 4, self.colors["title"])
        return Rect(rect.y + 1, rect.x + 2, rect.h - 2, rect.w - 4)

    def render(self, state: DashboardState, action: ActionInfo | None, logs: list[str],
               wizard: WizardState | None = None, modal: str = "") -> None:
        self.screen.erase()
        self.regions = []
        height, width = self.screen.getmaxyx()
        if wizard is not None:
            self._wizard(state, wizard, height, width)
        else:
            mode = layout_mode(width, height)
            self._header(state, action, width)
            if mode == "small":
                self._small(height, width)
            elif mode == "large":
                self._large(state, action, logs, height, width)
            else:
                self._compact(state, action, logs, height, width)
            self._footer(state, height, width)
        if modal:
            self._modal(modal, height, width)
        self.screen.noutrefresh()
        curses.doupdate()

    def _header(self, state: DashboardState, action: ActionInfo | None, width: int) -> None:
        mode_attr = self.colors["bad"] if "ERROR" in state.mode else self.colors["good"]
        self.text(0, 1, "VM HELPER", 12, self.colors["title"])
        self.text(0, 15, state.mode, max(1, width - 40), mode_attr)
        worker = f"ACTION {action.action}: {action.status}" if action else "idle"
        self.text(0, max(1, width - len(worker) - 2), worker, width - 2, self.colors["warn"] if action and action.active else self.colors["dim"])
        self.text(1, 1, "-" * max(0, width - 2), width - 2, self.colors["dim"])

    def _footer(self, state: DashboardState, height: int, width: int) -> None:
        help_text = "Arrows/jk move  Enter select  Tab panels  r refresh  c configure  q quit"
        message = state.error or state.notice or help_text
        attr = self.colors["bad"] if state.error else self.colors["warn"] if state.notice else self.colors["dim"]
        self.text(height - 1, 1, message, width - 2, attr)

    def _small(self, height: int, width: int) -> None:
        lines = ("Terminal too small for the dashboard.", "Resize to at least 80 x 24.", "q or Esc still exits safely.")
        for offset, line in enumerate(lines):
            self.text(max(3, height // 2 - 1 + offset), max(1, (width - len(line)) // 2), line, width - 2,
                      self.colors["warn"] if offset < 2 else self.colors["dim"])

    def _large(self, state: DashboardState, action: ActionInfo | None, logs: list[str], height: int, width: int) -> None:
        top, body_h = 2, height - 3
        left_w = max(40, width // 3)
        right_w = width - left_w
        left_heights = (10, 11, body_h - 21)
        right_heights = (9, 11, body_h - 20)
        self._actions(state, action, self.box(Rect(top, 0, left_heights[0], left_w), "Actions"))
        self._host(state, self.box(Rect(top + left_heights[0], 0, left_heights[1], left_w), "Host"))
        self._usb(state, self.box(Rect(top + sum(left_heights[:2]), 0, left_heights[2], left_w), "USB"))
        self._vm(state, self.box(Rect(top, left_w, right_heights[0], right_w), "Virtual Machine"))
        self._gpu_io(state, self.box(Rect(top + right_heights[0], left_w, right_heights[1], right_w), "GPU / I/O"))
        self._logs(logs, state.log_scroll, self.box(Rect(top + sum(right_heights[:2]), left_w, right_heights[2], right_w), "Action Log"))

    def _compact(self, state: DashboardState, action: ActionInfo | None, logs: list[str], height: int, width: int) -> None:
        self._action_bar(state, action, 2, width)
        x = 1
        for index, tab in enumerate(COMPACT_TABS):
            label = f" {tab} "
            attr = self.colors["select"] if index == state.tab else self.colors["dim"]
            self.text(5, x, label, len(label), attr)
            self.regions.append(MouseRegion(5, x, 5, x + len(label) - 1, f"tab:{index}"))
            x += len(label) + 1
        area = self.box(Rect(6, 0, height - 7, width), COMPACT_TABS[state.tab])
        tab = COMPACT_TABS[state.tab]
        if tab == "Overview":
            split = max(7, area.h // 2)
            self._host(state, Rect(area.y, area.x, split, area.w))
            self._vm(state, Rect(area.y + split, area.x, area.h - split, area.w))
        elif tab == "GPU / I/O":
            self._gpu_io(state, area)
        elif tab == "USB":
            self._usb(state, area)
        else:
            self._logs(logs, state.log_scroll, area)

    def _action_bar(self, state: DashboardState, action: ActionInfo | None, y: int, width: int) -> None:
        x = 1
        for index, spec in enumerate(ACTIONS):
            label = f" {index + 1}:{spec.label} "
            disabled = bool(action and action.active)
            attr = self.colors["dim"] if disabled else self.colors["select"] if index == state.selected_action else 0
            if x + len(label) >= width:
                y += 1; x = 1
            self.text(y, x, label, len(label), attr)
            self.regions.append(MouseRegion(y, x, y, x + len(label) - 1, f"action:{index}"))
            x += len(label) + 1

    def _actions(self, state: DashboardState, action: ActionInfo | None, area: Rect) -> None:
        for index, spec in enumerate(ACTIONS[:area.h]):
            disabled = bool(action and action.active)
            marker = ">" if index == state.selected_action else " "
            label = f"{marker} {index + 1} [{spec.hotkey}] {spec.label}"
            attr = self.colors["dim"] if disabled else self.colors["select"] if index == state.selected_action else 0
            self.text(area.y + index, area.x, label, area.w, attr)
            self.regions.append(MouseRegion(area.y + index, area.x, area.y + index, area.x + area.w - 1, f"action:{index}"))

    def _host(self, state: DashboardState, area: Rect) -> None:
        host = state.host; hist = state.histories
        memory_pct = host.memory_used * 100.0 / host.memory_total if host.memory_total else 0.0
        rows = [
            f"CPU {host.cpu:5.1f}% {sparkline(hist.cpu, max(4, area.w - 16), 100)}",
            f"MEM {memory_pct:5.1f}% {bytes_text(host.memory_used)} / {bytes_text(host.memory_total)}",
            f"Load {host.load[0]:.2f} {host.load[1]:.2f} {host.load[2]:.2f}   up {duration_text(host.uptime)}",
            f"{host.interface} RX {bytes_text(host.net_rx)}/s  TX {bytes_text(host.net_tx)}/s",
            f"Disk R {bytes_text(host.disk_read)}/s  W {bytes_text(host.disk_write)}/s",
        ]
        for index, row in enumerate(rows[:area.h]):
            self.text(area.y + index, area.x, row, area.w)
        if area.h > len(rows) and host.cores:
            core_width = 10
            per_row = max(1, area.w // core_width)
            for row_index in range(area.h - len(rows)):
                start = row_index * per_row
                values = host.cores[start:start + per_row]
                if not values:
                    break
                line = " ".join(f"c{start + offset:02d} {value:3.0f}%" for offset, value in enumerate(values))
                self.text(area.y + len(rows) + row_index, area.x, line, area.w, self.colors["dim"])

    def _vm(self, state: DashboardState, area: Rect) -> None:
        vm = state.vm; rates = state.vm_rates
        current = safe_int(vm[5]) * 1024 if len(vm) > 5 else 0
        maximum = safe_int(vm[6]) * 1024 if len(vm) > 6 else 0
        rows = [
            f"{vm[0]}  state={vm[1]}  vCPUs={vm[3] if len(vm) > 3 else '0'}",
            f"CPU {rates.cpu:5.1f}% {sparkline(state.histories.vm_cpu, max(4, area.w - 15), 100)}",
            f"Memory {bytes_text(current)} / {bytes_text(maximum)}",
            f"Network RX {bytes_text(rates.net_rx)}/s  TX {bytes_text(rates.net_tx)}/s",
            f"Block   R {bytes_text(rates.block_read)}/s  W {bytes_text(rates.block_write)}/s",
        ]
        if state.inventory.pins:
            summary = " ".join(f"{vcpu}={cpus}" for vcpu, cpus in state.inventory.pins[:4])
            rows.append(f"Pinning {len(state.inventory.pins)} vCPUs: {summary}")
        for index, row in enumerate(rows[:area.h]):
            self.text(area.y + index, area.x, row, area.w)

    def _gpu_io(self, state: DashboardState, area: Rect) -> None:
        grouped = state.grouped; row = 0
        for gpu in grouped.get("gpu", []):
            if row >= area.h:
                break
            self.text(area.y + row, area.x, f"{gpu[0]}  {gpu[1]}  IOMMU {gpu[2]}  {gpu[3]}", area.w)
            row += 1
        nvidia = grouped.get("nvidia", [])
        if nvidia and row < area.h:
            item = nvidia[0]
            self.text(area.y + row, area.x, f"NVIDIA {item[0]} C  util {item[1]}%  VRAM {item[2]}/{item[3]} MiB  {item[4]} W", area.w)
            row += 1
        for disk in grouped.get("disk", []):
            if row >= area.h:
                break
            health = "OK" if disk[2] == "yes" and disk[3] != "offline" and not disk[4] else "UNSAFE"
            attr = self.colors["good"] if health == "OK" else self.colors["bad"]
            self.text(area.y + row, area.x, f"Disk {health} state={disk[3]} {disk[0]}", area.w, attr)
            row += 1
        if row == 0:
            self.text(area.y, area.x, "GPU and disk data unavailable", area.w, self.colors["warn"])

    def _usb(self, state: DashboardState, area: Rect) -> None:
        devices = state.grouped.get("usb", [])
        if not devices:
            self.text(area.y, area.x, "No configured USB devices", area.w, self.colors["dim"])
            return
        for index, item in enumerate(devices[:area.h]):
            status = "VM" if item[4] == "yes" else "Linux" if item[3] == "yes" else "missing"
            attr = self.colors["good"] if item[3] == "yes" else self.colors["bad"]
            self.text(area.y + index, area.x, f"{item[1]}:{item[2]} {status:7} {item[0]}", area.w, attr)

    def _logs(self, logs: list[str], scroll: int, area: Rect) -> None:
        end = max(0, len(logs) - scroll)
        start = max(0, end - area.h)
        visible = logs[start:end]
        for index, line in enumerate(visible):
            self.text(area.y + index, area.x, line, area.w)
        if not visible:
            self.text(area.y, area.x, "No action log yet", area.w, self.colors["dim"])
        if scroll:
            self.text(area.y, max(area.x, area.x + area.w - 15), f"[{scroll} lines back]", 15, self.colors["warn"])

    def _wizard(self, state: DashboardState, wizard: WizardState, height: int, width: int) -> None:
        self.text(0, 1, "VM HELPER CONFIGURATION", width - 2, self.colors["title"])
        side = 19
        for index, step in enumerate(WIZARD_STEPS):
            marker = ">" if index == wizard.step else " "
            attr = self.colors["select"] if index == wizard.step else self.colors["dim"]
            self.text(2 + index, 1, f"{marker} {index + 1}. {step}", side - 2, attr)
        area = self.box(Rect(1, side, height - 3, width - side), WIZARD_STEPS[wizard.step])
        inv = state.inventory
        if wizard.step == 0:
            self._choices(area, [f"{name}  ({status})" for name, status in inv.domains], wizard.cursor)
        elif wizard.step == 1:
            rows = [f"{item[0]} driver={item[1]} IOMMU={item[2]} persistent={item[4]} {item[3]}" for item in inv.gpus]
            self._choices(area, rows, wizard.cursor)
            if inv.gpus:
                selected = inv.gpus[wizard.cursor][0]
                base = min(area.y + len(rows) + 1, area.y + area.h - 1)
                for offset, item in enumerate(inv.companions.get(selected, [])):
                    self.text(base + offset, area.x, f"companion {item[1]} driver={item[2]} persistent={item[5]} {item[4]}", area.w, self.colors["dim"])
        elif wizard.step == 2:
            rows = [f"{item[0]} -> {item[1] or '?'} exists={item[2]} state={item[3]} mounted={'yes' if item[4] else 'no'}" for item in inv.disks]
            self._choices(area, rows or ["No raw block disks (file-backed VM)"], 0)
        elif wizard.step == 3:
            for index, item in enumerate(inv.usb[:area.h]):
                usb_id = f"{item[1]}:{item[2]}"; checked = "x" if usb_id in wizard.usb_ids else " "
                attr = self.colors["select"] if index == wizard.cursor else 0
                self.text(area.y + index, area.x, f"[{checked}] {usb_id} {item[0]}", area.w, attr)
        elif wizard.step in (4, 5):
            rows = [f"Domain: {wizard.vm}", f"GPU: {wizard.gpu}",
                    f"USB: {', '.join(sorted(wizard.usb_ids)) or 'none'}",
                    f"Raw disks: {len(inv.disks)}", f"vCPU pins: {len(inv.pins)}"]
            rows.extend(f"Timer {name}: {value}s" for name, value in inv.timers.items())
            warnings = wizard.persistence_warnings(inv)
            rows.extend(f"WARNING: {warning}" for warning in warnings)
            if wizard.step == 5:
                rows.extend(("", "Press Enter to write the backed-up mode-0600 vm-helper.env.", "Press Esc to go back."))
            for index, row in enumerate(rows[:area.h]):
                attr = self.colors["bad"] if row.startswith("WARNING") else self.colors["warn"] if "Press Enter" in row else 0
                self.text(area.y + index, area.x, row, area.w, attr)
        footer = wizard.message or "Up/Down select  Space toggle USB  Enter next  Left/Esc back  q cancel"
        self.text(height - 1, 1, footer, width - 2, self.colors["bad"] if wizard.message else self.colors["dim"])

    def _choices(self, area: Rect, rows: Iterable[str], selected: int) -> None:
        for index, row in enumerate(list(rows)[:area.h]):
            attr = self.colors["select"] if index == selected else 0
            self.text(area.y + index, area.x, row, area.w, attr)

    def _modal(self, prompt: str, height: int, width: int) -> None:
        box_width = min(max(42, len(prompt) + 6), max(4, width - 4))
        rect = Rect(max(1, height // 2 - 2), max(0, (width - box_width) // 2), 5, box_width)
        area = self.box(rect, "Confirm")
        self.text(area.y, area.x, prompt, area.w)
        self.text(area.y + 2, area.x, "y confirm    n / Esc cancel", area.w, self.colors["warn"])
