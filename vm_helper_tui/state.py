"""Application state, panel registry, input mapping, and wizard model."""

from __future__ import annotations

from dataclasses import dataclass, field
import curses
import time
from typing import Iterable

from .protocol import Record, records_by_kind
from .telemetry import Histories, HostSample, VmRates, derive_mode


@dataclass(frozen=True)
class MenuSpec:
    label: str
    hotkey: str
    kind: str
    worker_action: str = ""
    mutating: bool = False
    confirmation: str = ""


MENU_ITEMS = (
    MenuSpec("Linux", "l", "worker", "linux", True, "Return devices to Linux and start the desktop?"),
    MenuSpec("Windows", "w", "worker", "windows", True, "Start Windows and leave Linux at TTY?"),
    MenuSpec("Linux + Windows", "b", "worker", "linux-windows", True, "Start Windows and the Linux desktop?"),
    MenuSpec("Validate", "v", "worker", "check", False, "Run the read-only transition preflight?"),
    MenuSpec("USB", "u", "usb"),
    MenuSpec("Configure", "c", "configure"),
    MenuSpec("Quit", "q", "quit"),
)


def action_label(action: str) -> str:
    if action == "usb-route":
        return "USB routing"
    return next((item.label for item in MENU_ITEMS if item.worker_action == action), action)


@dataclass(frozen=True)
class PanelSpec:
    key: str
    title: str
    order: int
    compact_tab: str


PANEL_REGISTRY = (
    PanelSpec("menu", "Menu", 10, "Overview"),
    PanelSpec("host", "Host", 20, "Overview"),
    PanelSpec("vm", "Virtual Machine", 30, "Overview"),
    PanelSpec("gpu_io", "GPU / I/O", 40, "GPU / I/O"),
    PanelSpec("usb", "USB", 50, "USB"),
    PanelSpec("logs", "Activity Log", 60, "Activity"),
)
COMPACT_TABS = tuple(dict.fromkeys(panel.compact_tab for panel in PANEL_REGISTRY))
DEFAULT_LAYOUT_PROFILE = {
    "large_columns": (("menu", "host", "usb"), ("vm", "gpu_io", "logs")),
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
        ord("a"): "apply",
        ord("["): "log_up",
        ord("]"): "log_down",
        ord(" "): "toggle",
    }
    for index, item in enumerate(MENU_ITEMS, 1):
        mapping[ord(str(index))] = f"menu:{index - 1}"
        if item.kind != "quit":
            mapping[ord(item.hotkey)] = f"menu:{index - 1}"
    return mapping.get(key)


@dataclass(frozen=True)
class UsbRoute:
    label: str
    vid: str
    pid: str
    present: bool
    configured: bool
    owner: str
    count: int
    blocked_reason: str = ""

    @property
    def usb_id(self) -> str:
        return f"{self.vid}:{self.pid}"

    @property
    def unavailable_reason(self) -> str:
        if self.blocked_reason:
            return self.blocked_reason
        if not self.present:
            return "device is missing"
        if self.count != 1:
            return f"{self.count} identical devices are connected"
        return ""


@dataclass
class Inventory:
    records: list[Record] = field(default_factory=list)
    domains: list[tuple[str, str]] = field(default_factory=list)
    gpus: list[tuple[str, ...]] = field(default_factory=list)
    companions: dict[str, list[tuple[str, ...]]] = field(default_factory=dict)
    usb: list[tuple[str, ...]] = field(default_factory=list)
    usb_routes: list[UsbRoute] = field(default_factory=list)
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
        self.usb_routes = [
            UsbRoute(
                label=item[0], vid=item[1], pid=item[2], present=item[3] == "yes",
                configured=item[4] == "yes", owner=item[5], count=max(0, int(item[6])),
                blocked_reason=item[8] if len(item) >= 9 and item[7] != "yes" else "",
            )
            for item in grouped.get("usb_route", []) if len(item) >= 7 and item[6].isdigit()
        ]
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
    selected_menu: int = 0
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

    def move_menu(self, delta: int) -> None:
        self.selected_menu = (self.selected_menu + delta) % len(MENU_ITEMS)

    def move_tab(self, delta: int) -> None:
        self.tab = (self.tab + delta) % len(COMPACT_TABS)


@dataclass
class UsbPageState:
    cursor: int = 0
    desired: dict[str, str] = field(default_factory=dict)
    message: str = ""

    def sync(self, inventory: Inventory) -> None:
        ids = {route.usb_id for route in inventory.usb_routes if not route.unavailable_reason}
        self.desired = {usb_id: owner for usb_id, owner in self.desired.items() if usb_id in ids}
        if inventory.usb_routes:
            self.cursor = min(self.cursor, len(inventory.usb_routes) - 1)
        else:
            self.cursor = 0

    def move(self, delta: int, inventory: Inventory) -> None:
        count = len(inventory.usb_routes)
        self.cursor = (self.cursor + delta) % count if count else 0
        self.message = ""

    def selected(self, inventory: Inventory) -> UsbRoute | None:
        if not inventory.usb_routes:
            return None
        return inventory.usb_routes[min(self.cursor, len(inventory.usb_routes) - 1)]

    def target(self, route: UsbRoute) -> str:
        return self.desired.get(route.usb_id, route.owner)

    def set_target(self, target: str, inventory: Inventory, vm_running: bool) -> bool:
        route = self.selected(inventory)
        if route is None:
            self.message = "No USB devices are available."
            return False
        if route.unavailable_reason:
            self.message = f"{route.usb_id}: {route.unavailable_reason}."
            return False
        if target == "Windows" and not vm_running:
            self.message = "Start Windows before routing a USB device to it."
            return False
        if target not in ("Linux", "Windows"):
            return False
        if target == route.owner:
            self.desired.pop(route.usb_id, None)
        else:
            self.desired[route.usb_id] = target
        self.message = ""
        return True

    def toggle(self, inventory: Inventory, vm_running: bool) -> bool:
        route = self.selected(inventory)
        target = "Windows" if route and self.target(route) == "Linux" else "Linux"
        return self.set_target(target, inventory, vm_running)

    def changes(self, inventory: Inventory) -> list[tuple[str, str]]:
        return [
            (route.usb_id, target)
            for route in inventory.usb_routes
            if (target := self.desired.get(route.usb_id)) and target != route.owner
        ]


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
