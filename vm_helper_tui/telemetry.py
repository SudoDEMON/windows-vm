"""Host and VM telemetry collectors with deterministic delta helpers."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import os
from pathlib import Path
import time
from typing import Callable, Iterable

from .protocol import Record, records_by_kind


SPARK_CHARS = " .:-=+*#%@"


def safe_int(value: str, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def counter_rate(current: int, previous: int | None, elapsed: float) -> float:
    if previous is None or elapsed <= 0 or current < previous:
        return 0.0
    return (current - previous) / elapsed


def sparkline(values: Iterable[float], width: int, ceiling: float | None = None) -> str:
    samples = list(values)[-max(0, width) :]
    if width <= 0:
        return ""
    if not samples:
        return " " * width
    scale = ceiling if ceiling and ceiling > 0 else max(samples)
    scale = max(scale, 1.0)
    rendered = "".join(SPARK_CHARS[min(len(SPARK_CHARS) - 1, int(max(0.0, value) / scale * (len(SPARK_CHARS) - 1)))] for value in samples)
    return rendered.rjust(width)


def derive_mode(vm_state: str, display_state: str, gpu_drivers: Iterable[str], error: bool = False) -> str:
    drivers = list(gpu_drivers)
    if error or not drivers or any(driver in ("", "none", "unknown") for driver in drivers):
        return "INCONSISTENT / ERROR"
    all_vfio = all(driver == "vfio-pci" for driver in drivers)
    all_host = all(driver != "vfio-pci" for driver in drivers)
    display_active = display_state == "active"
    vm_running = vm_state in {"running", "paused", "in shutdown"}
    vm_off = vm_state == "shut off"
    if vm_off and display_active and all_host:
        return "LINUX"
    if vm_running and not display_active and all_vfio:
        return "WINDOWS"
    if vm_running and display_active and all_vfio:
        return "LINUX + WINDOWS"
    if vm_off and not display_active and all_host:
        return "LINUX TTY"
    return "INCONSISTENT / ERROR"


@dataclass
class HostSample:
    cpu: float = 0.0
    cores: list[float] = field(default_factory=list)
    memory_used: int = 0
    memory_total: int = 0
    load: tuple[float, float, float] = (0.0, 0.0, 0.0)
    uptime: float = 0.0
    interface: str = "none"
    net_rx: float = 0.0
    net_tx: float = 0.0
    disk_read: float = 0.0
    disk_write: float = 0.0


class HostCollector:
    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self.clock = clock
        self.previous_time: float | None = None
        self.previous_cpu: dict[str, tuple[int, int]] = {}
        self.previous_net: dict[str, tuple[int, int]] = {}
        self.previous_disk: tuple[int, int] | None = None

    @staticmethod
    def _cpu_counters() -> dict[str, tuple[int, int]]:
        counters: dict[str, tuple[int, int]] = {}
        try:
            lines = Path("/proc/stat").read_text().splitlines()
        except OSError:
            return counters
        for line in lines:
            fields = line.split()
            if not fields or not fields[0].startswith("cpu"):
                continue
            values = [safe_int(value) for value in fields[1:]]
            total = sum(values)
            idle = sum(values[index] for index in (3, 4) if index < len(values))
            counters[fields[0]] = (total, idle)
        return counters

    @staticmethod
    def _cpu_percent(current: tuple[int, int], previous: tuple[int, int] | None) -> float:
        if previous is None:
            return 0.0
        total = current[0] - previous[0]
        idle = current[1] - previous[1]
        return max(0.0, min(100.0, (total - idle) * 100.0 / total)) if total > 0 else 0.0

    @staticmethod
    def _memory() -> tuple[int, int]:
        values: dict[str, int] = {}
        try:
            for line in Path("/proc/meminfo").read_text().splitlines():
                key, raw = line.split(":", 1)
                values[key] = safe_int(raw.split()[0]) * 1024
        except (OSError, IndexError, ValueError):
            return 0, 0
        total = values.get("MemTotal", 0)
        available = values.get("MemAvailable", values.get("MemFree", 0))
        return max(0, total - available), total

    @staticmethod
    def _default_interface() -> str:
        try:
            for line in Path("/proc/net/route").read_text().splitlines()[1:]:
                fields = line.split()
                if len(fields) > 3 and fields[1] == "00000000" and int(fields[3], 16) & 2:
                    return fields[0]
        except (OSError, ValueError):
            pass
        return ""

    @staticmethod
    def _network() -> dict[str, tuple[int, int]]:
        counters: dict[str, tuple[int, int]] = {}
        try:
            lines = Path("/proc/net/dev").read_text().splitlines()[2:]
        except OSError:
            return counters
        for line in lines:
            name, raw = line.split(":", 1)
            fields = raw.split()
            if len(fields) >= 9:
                counters[name.strip()] = (safe_int(fields[0]), safe_int(fields[8]))
        return counters

    @staticmethod
    def _disk() -> tuple[int, int]:
        read_sectors = write_sectors = 0
        try:
            lines = Path("/proc/diskstats").read_text().splitlines()
        except OSError:
            return 0, 0
        for line in lines:
            fields = line.split()
            if len(fields) < 14:
                continue
            name = fields[2]
            if name.startswith(("loop", "ram", "zram", "dm-")) or name[-1:].isdigit() and not name.startswith("nvme"):
                continue
            if name.startswith("nvme") and "p" in name:
                continue
            read_sectors += safe_int(fields[5])
            write_sectors += safe_int(fields[9])
        return read_sectors * 512, write_sectors * 512

    def sample(self) -> HostSample:
        now = self.clock()
        elapsed = now - self.previous_time if self.previous_time is not None else 0.0
        cpu = self._cpu_counters()
        memory_used, memory_total = self._memory()
        network = self._network()
        interface = self._default_interface()
        if interface not in network:
            interface = max((name for name in network if name != "lo"), key=lambda name: sum(network[name]), default="none")
        current_net = network.get(interface, (0, 0))
        previous_net = self.previous_net.get(interface)
        disk = self._disk()
        sample = HostSample(
            cpu=self._cpu_percent(cpu.get("cpu", (0, 0)), self.previous_cpu.get("cpu")),
            cores=[self._cpu_percent(cpu[name], self.previous_cpu.get(name)) for name in sorted(cpu) if name != "cpu"],
            memory_used=memory_used,
            memory_total=memory_total,
            load=os.getloadavg(),
            uptime=float(Path("/proc/uptime").read_text().split()[0]) if Path("/proc/uptime").is_file() else 0.0,
            interface=interface,
            net_rx=counter_rate(current_net[0], previous_net[0] if previous_net else None, elapsed),
            net_tx=counter_rate(current_net[1], previous_net[1] if previous_net else None, elapsed),
            disk_read=counter_rate(disk[0], self.previous_disk[0] if self.previous_disk else None, elapsed),
            disk_write=counter_rate(disk[1], self.previous_disk[1] if self.previous_disk else None, elapsed),
        )
        self.previous_time = now
        self.previous_cpu = cpu
        self.previous_net = network
        self.previous_disk = disk
        return sample


@dataclass
class VmRates:
    cpu: float = 0.0
    net_rx: float = 0.0
    net_tx: float = 0.0
    block_read: float = 0.0
    block_write: float = 0.0


class VmDeltaTracker:
    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self.clock = clock
        self.previous_time: float | None = None
        self.previous: dict[str, int] = {}

    def update(self, records: Iterable[Record]) -> VmRates:
        now = self.clock()
        elapsed = now - self.previous_time if self.previous_time is not None else 0.0
        grouped = records_by_kind(records)
        vm = grouped.get("vm", [("", "", "", "0", "0", "0", "0")])[0]
        counters = {
            "cpu": safe_int(vm[4]) if len(vm) > 4 else 0,
            "rx": sum(safe_int(item[1]) for item in grouped.get("vm_net", []) if len(item) > 2),
            "tx": sum(safe_int(item[2]) for item in grouped.get("vm_net", []) if len(item) > 2),
            "read": sum(safe_int(item[2]) for item in grouped.get("vm_block", []) if len(item) > 3),
            "write": sum(safe_int(item[3]) for item in grouped.get("vm_block", []) if len(item) > 3),
        }
        rates = VmRates(
            cpu=counter_rate(counters["cpu"], self.previous.get("cpu"), elapsed) / 10_000_000,
            net_rx=counter_rate(counters["rx"], self.previous.get("rx"), elapsed),
            net_tx=counter_rate(counters["tx"], self.previous.get("tx"), elapsed),
            block_read=counter_rate(counters["read"], self.previous.get("read"), elapsed),
            block_write=counter_rate(counters["write"], self.previous.get("write"), elapsed),
        )
        self.previous_time = now
        self.previous = counters
        return rates


@dataclass
class Histories:
    cpu: deque[float] = field(default_factory=lambda: deque(maxlen=60))
    memory: deque[float] = field(default_factory=lambda: deque(maxlen=60))
    net: deque[float] = field(default_factory=lambda: deque(maxlen=60))
    disk: deque[float] = field(default_factory=lambda: deque(maxlen=60))
    vm_cpu: deque[float] = field(default_factory=lambda: deque(maxlen=60))

    def add(self, host: HostSample, vm: VmRates) -> None:
        self.cpu.append(host.cpu)
        self.memory.append(host.memory_used * 100.0 / host.memory_total if host.memory_total else 0.0)
        self.net.append(host.net_rx + host.net_tx)
        self.disk.append(host.disk_read + host.disk_write)
        self.vm_cpu.append(vm.cpu)
