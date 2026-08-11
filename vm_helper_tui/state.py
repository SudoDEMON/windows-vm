"""Application state, panel registry, input mapping, and wizard model."""

from __future__ import annotations

from dataclasses import dataclass, field
import curses
import time
from typing import Iterable

from .protocol import Record, records_by_kind
from .telemetry import Histories, HostSample, VmRates, derive_mode


@dataclass(frozen=True)
class ActionSpec:
    label: str
    worker_action: str
    hotkey: str
    mutating: bool
    confirmation: str


ACTIONS = (
    ActionSpec("Linux", "linux", "l", True, "Return devices to Linux and start the desktop?"),
    ActionSpec("Windows", "windows", "w", True, "Start Windows and leave Linux at TTY?"),
    ActionSpec("Linux + Windows", "linux-windows", "b", True, "Start Windows and the Linux desktop?"),
    ActionSpec("Validate", "check", "v", False, "Run the read-only transition preflight?"),
    ActionSpec("USB -> Windows", "usb-attach", "u", True, "Attach configured USB devices to Windows?"),
    ActionSpec("USB -> Linux", "usb-detach", "d", True, "Detach configured USB devices to Linux?"),
)


@dataclass(frozen=True)
class PanelSpec:
    key: str
    title: str
    order: int
    compact_tab: str


PANEL_REGISTRY = (
    PanelSpec("actions", "Actions", 10, "Overview"),
    PanelSpec("host", "Host", 20, "Overview"),
    PanelSpec("vm", "Virtual Machine", 30, "Overview"),
    PanelSpec("gpu_io", "GPU / I/O", 40, "GPU / I/O"),
    PanelSpec("usb", "USB", 50, "USB"),
    PanelSpec("logs", "Action Log", 60, "Logs"),
)
COMPACT_TABS = tuple(dict.fromkeys(panel.compact_tab for panel in PANEL_REGISTRY))
DEFAULT_LAYOUT_PROFILE = {
    "large_columns": (("actions", "host", "usb"), ("vm", "gpu_io", "logs")),
    "compact_tabs": COMPACT_TABS,
}


def layout_mode(width: int, height: int) -> str:
    if width >= 120 and height >= 35:
        return "large"
    if width >= 80 and height >= 24:
        return "compact"
    return "small"


@dataclass(frozen=True)
class MouseRegion:
    y1: int
    x1: int
    y2: int
    x2: int
    command: str

    def contains(self, y: int, x: int) -> bool:
        return self.y1 <= y <= self.y2 and self.x1 <= x <= self.x2


def map_mouse(y: int, x: int, button_state: int, regions: Iterable[MouseRegion]) -> str | None:
    if button_state & getattr(curses, "BUTTON4_PRESSED", 0):
        return "log_up"
    if button_state & getattr(curses, "BUTTON5_PRESSED", 0):
        return "log_down"
    accepted = getattr(curses, "BUTTON1_CLICKED", 0) | getattr(curses, "BUTTON1_PRESSED", 0)
    if not button_state & accepted:
        return None
    return next((region.command for region in regions if region.contains(y, x)), None)


def map_key(key: int) -> str | None:
    mapping = {
        curses.KEY_UP: "up",
        curses.KEY_DOWN: "down",
        curses.KEY_LEFT: "previous_tab",
        curses.KEY_RIGHT: "next_tab",
        curses.KEY_ENTER: "activate",
        10: "activate",
        13: "activate",
        9: "next_tab",
        getattr(curses, "KEY_BTAB", 353): "previous_tab",
        curses.KEY_PPAGE: "log_up",
        curses.KEY_NPAGE: "log_down",
        curses.KEY_RESIZE: "resize",
        27: "escape",
        ord("q"): "quit",
        ord("j"): "down",
        ord("k"): "up",
        ord("r"): "refresh",
        ord("c"): "configure",
        ord("["): "log_up",
        ord("]"): "log_down",
        ord(" "): "toggle",
    }
    for index, action in enumerate(ACTIONS, 1):
        mapping[ord(str(index))] = f"action:{index - 1}"
        mapping[ord(action.hotkey)] = f"action:{index - 1}"
    return mapping.get(key)


@dataclass
class Inventory:
    records: list[Record] = field(default_factory=list)
    domains: list[tuple[str, str]] = field(default_factory=list)
    gpus: list[tuple[str, ...]] = field(default_factory=list)
    companions: dict[str, list[tuple[str, ...]]] = field(default_factory=dict)
    usb: list[tuple[str, ...]] = field(default_factory=list)
    disks: list[tuple[str, ...]] = field(default_factory=list)
    pins: list[tuple[str, str]] = field(default_factory=list)
    timers: dict[str, str] = field(default_factory=dict)
    config: tuple[str, ...] = ()

    def update(self, records: list[Record]) -> None:
        grouped = records_by_kind(records)
        self.records = records
        self.domains = [item for item in grouped.get("domain", []) if len(item) >= 2]
        self.gpus = [item for item in grouped.get("gpu", []) if len(item) >= 6]
        self.companions = {}
        for item in grouped.get("companion", []):
            if len(item) >= 6:
                self.companions.setdefault(item[0], []).append(item)
        self.usb = [item for item in grouped.get("usb_inventory", []) if len(item) >= 4]
        self.disks = [item for item in grouped.get("disk", []) if len(item) >= 5]
        self.pins = [(item[0], item[1]) for item in grouped.get("pin", []) if len(item) >= 2]
        self.timers = {item[0]: item[1] for item in grouped.get("timer", []) if len(item) >= 2}
        self.config = grouped.get("config", [()])[0]


@dataclass
class DashboardState:
    snapshot: list[Record] = field(default_factory=list)
    inventory: Inventory = field(default_factory=Inventory)
    host: HostSample = field(default_factory=HostSample)
    vm_rates: VmRates = field(default_factory=VmRates)
    histories: Histories = field(default_factory=Histories)
    error: str = ""
    notice: str = ""
    selected_action: int = 0
    tab: int = 0
    log_scroll: int = 0
    last_dynamic: float = 0.0
    last_static: float = 0.0

    @property
    def grouped(self) -> dict[str, list[tuple[str, ...]]]:
        return records_by_kind(self.snapshot)

    @property
    def vm(self) -> tuple[str, ...]:
        return self.grouped.get("vm", [("unconfigured", "unknown", "", "0", "0", "0", "0")])[0]

    @property
    def mode(self) -> str:
        grouped = self.grouped
        display = grouped.get("display", [("unknown",)])[0][0]
        drivers = [item[1] for item in grouped.get("gpu", []) if len(item) > 1]
        return derive_mode(self.vm[1], display, drivers, bool(self.error))

    def apply_snapshot(self, records: list[Record], host: HostSample, vm_rates: VmRates) -> None:
        self.snapshot = records
        self.host = host
        self.vm_rates = vm_rates
        self.histories.add(host, vm_rates)
        self.error = ""
        self.last_dynamic = time.monotonic()

    def move_action(self, delta: int) -> None:
        self.selected_action = (self.selected_action + delta) % len(ACTIONS)

    def move_tab(self, delta: int) -> None:
        self.tab = (self.tab + delta) % len(COMPACT_TABS)


WIZARD_STEPS = ("Domain", "GPU", "Raw disks", "USB", "Review", "Write")


@dataclass
class WizardState:
    step: int = 0
    cursor: int = 0
    vm: str = ""
    gpu: str = ""
    usb_ids: set[str] = field(default_factory=set)
    message: str = ""

    def item_count(self, inventory: Inventory) -> int:
        if self.step == 0:
            return len(inventory.domains)
        if self.step == 1:
            return len(inventory.gpus)
        if self.step == 3:
            return len(inventory.usb)
        return 1

    def move(self, delta: int, inventory: Inventory) -> None:
        count = self.item_count(inventory)
        self.cursor = (self.cursor + delta) % count if count else 0

    def toggle(self, inventory: Inventory) -> None:
        if self.step != 3 or not inventory.usb:
            return
        item = inventory.usb[self.cursor]
        usb_id = f"{item[1]}:{item[2]}"
        if usb_id in self.usb_ids:
            self.usb_ids.remove(usb_id)
        else:
            self.usb_ids.add(usb_id)

    def advance(self, inventory: Inventory) -> bool:
        self.message = ""
        if self.step == 0:
            if not inventory.domains:
                self.message = "No libvirt domains were found."
                return False
            self.vm = inventory.domains[self.cursor][0]
        elif self.step == 1:
            if not inventory.gpus:
                self.message = "No display GPUs were found."
                return False
            self.gpu = inventory.gpus[self.cursor][0]
        elif self.step == 3:
            pass
        elif self.step == len(WIZARD_STEPS) - 1:
            return True
        self.step += 1
        self.cursor = 0
        return False

    def back(self) -> bool:
        if self.step == 0:
            return True
        self.step -= 1
        self.cursor = 0
        self.message = ""
        return False

    def persistence_warnings(self, inventory: Inventory) -> list[str]:
        warnings: list[str] = []
        gpu = next((item for item in inventory.gpus if item[0] == self.gpu), None)
        if gpu and gpu[4] != "yes":
            warnings.append(f"{self.gpu} is not a persistent VM hostdev")
        for companion in inventory.companions.get(self.gpu, []):
            if companion[5] != "yes":
                warnings.append(f"{companion[1]} companion is not persistent")
        return warnings

    def selected_usb_rows(self, inventory: Inventory) -> list[tuple[str, ...]]:
        return [item for item in inventory.usb if f"{item[1]}:{item[2]}" in self.usb_ids]
