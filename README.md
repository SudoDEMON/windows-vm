# Interactive VM Helper

`vm-helper` combines a persistent Python `curses` dashboard with one safety-critical Bash backend for switching a Linux host and a libvirt VM around passed-through GPU and USB devices. The dashboard uses only the Python standard library; every ownership check and mutation remains in `vm-helper`. Familiar legacy command names remain symlinks to that backend.

The tool does not create or edit a Windows VM. The libvirt domain must already exist, and its selected GPU PCI functions must be persistent managed host devices. Both raw-block and file-backed VM disks are supported.

## Start

Launch the monitoring dashboard:

```bash
./vm-helper
```

`vm-helper` and `vm-helper menu` open the TUI. `vm-helper configure` opens its full-screen configuration wizard. Direct commands remain scriptable and compatible:

```bash
./vm-helper linux
./vm-helper windows
./vm-helper linux-windows
./vm-helper status
./vm-helper check
./vm-helper hardware
./vm-helper configure
./vm-helper usb status
./vm-helper usb xml
./vm-helper usb attach
./vm-helper usb detach
```

Use the dependency-free Bash interface explicitly when recovering a minimal host:

```bash
./vm-helper classic-menu
./vm-helper configure-classic
VM_HELPER_TUI=0 ./vm-helper
```

Python/curses absence, `TERM=dumb`, or non-interactive input automatically selects the classic behavior where appropriate.

## Dashboard

The dashboard refreshes dynamic counters every second, retains 60 samples for ASCII sparklines, and refreshes hardware/configuration discovery every five seconds. These read-only backend polls run asynchronously so a slow `virsh`, `nvidia-smi`, or hardware scan cannot stall keyboard input. Press `r` for an immediate full refresh.

- Host: aggregate and per-core CPU, memory, load, uptime, active-interface throughput, aggregate physical-disk I/O, and raw VM-disk health.
- VM: state, vCPU/memory counters, CPU utilization, network/block throughput, and configured vCPU pinning.
- Ownership: current Linux/Windows mode, display-manager state, PCI driver and IOMMU ownership, NVIDIA telemetry while host-owned, and USB presence/attachment.
- Menu: Linux, Windows, Linux + Windows, read-only validation, live USB routing, configuration, and Quit.

At 120x35 or larger, all panels remain visible. From 80x24 through the large-layout threshold, panels use tabs. Smaller terminals show a resize message while `q` and Esc continue to work. The renderer uses portable ASCII borders and adapts to 8-color, 256-color, Linux-console, SSH, and graphical terminals.

Keyboard controls:

- Arrows or `j`/`k`: move through the seven visible menu entries; Tab/Shift-Tab changes compact panels.
- Enter, numbers `1`-`7`, or displayed hotkeys: choose a menu entry; `y` confirms actions.
- Space: toggle USB selections in the wizard; Esc/Left goes back.
- Page Up/Page Down or `[`/`]`: scroll the integrated Activity Log without changing arrow-key menu navigation.
- `r`: refresh; `q` exits from the dashboard and returns from an in-app page.

Mouse selection and log-wheel scrolling are enabled when the terminal reports mouse events. Keyboard operation is always available.

`check` is a read-only transition preflight. It validates persistent PCI hostdev membership, PCI driver consistency, raw-disk safety, configured USB presence, and vendor GPU health without changing ownership.

Actions stay on the dashboard and stream into Activity Log. The header and persistent status row distinguish `RUNNING`, `SUCCEEDED`, `FAILED (exit N)`, and a stopped worker with incomplete metadata; the separate footer row always keeps navigation help visible. Closing the dashboard never cancels a detached action.

Selecting **USB** opens a full page inside the same curses session. It lists every connected non-hub USB VID:PID plus configured defaults that are currently missing, marks configuration defaults, and shows live Linux/Windows ownership. Up/Down selects a row, Left/Right chooses its staged owner, Space toggles it, and Enter or `a` applies all staged changes after one confirmation. Windows must already be running. Missing devices and duplicate VID:PID groups are shown but cannot be routed safely. Live routing never changes the defaults selected under **Configure**.

Legacy commands resolve to the same implementation:

```bash
./windows4090     # vm-helper windows
./linux-vm        # vm-helper linux-windows
./linuxvm         # vm-helper linux-windows
./linux4090       # vm-helper linux
./usb-vm-helper   # vm-helper usb ...
./vm-gpu-manager  # compatibility name for vm-helper
```

## Dynamic Discovery

The tool reads live host and libvirt state instead of assuming a 4090, fixed PCI addresses, CPU numbering, disk name, or USB set.

- VM domains come from `virsh` and are auto-selected when only one exists.
- GPU PCI functions come from display-class managed host devices in inactive VM XML, parsed with XPath.
- The configuration wizard lists every display GPU, current driver, companion function in its IOMMU group, and IOMMU group number.
- Host GPU modules are inferred for NVIDIA, AMD, and Intel devices.
- CPU model/topology and current VM vCPU pinning are displayed from `lscpu` and `virsh vcpupin`.
- Every raw block disk is discovered from inactive VM XML and checked; file-backed VMs skip raw-disk checks.
- USB IDs come from persistent VM XML or an interactive scan of connected devices.
- USB hostdev XML is generated at runtime, so static per-device XML files are unnecessary.

With exactly one configured VM, read-only status works without `vm-helper.env`:

```bash
VM_MANAGER_CONFIG=/nonexistent ./vm-helper status
```

For multiple VMs or explicit hardware choices, run the full-screen wizard:

```bash
./vm-helper configure
```

The in-app wizard selects a libvirt domain, reviews a GPU with its companion functions/IOMMU state, reviews attached raw disks, multi-selects the default USB devices for Windows startup, and shows persistence warnings, CPU pinning, and transition timers before writing. Its validated Bash endpoint writes ignored, mode-`0600` `vm-helper.env` and backs up an existing file before replacement. It does not edit libvirt XML. `vm-helper.env.example` documents every override. The former `REQUIRED_USB` array remains accepted for compatibility.

## Modes

`Windows` (`windows`) validates the VM, selected PCI hostdevs, every raw VM disk, USB presence, GUI state, and PCI driver consistency. It stops the display manager, checks GPU users, safely unloads the NVIDIA stack when applicable, starts the VM, verifies every GPU function reached `vfio-pci`, and live-attaches configured USB devices. Linux remains at TTY.

`Linux + Windows` (`linux-windows`, also `coexist` or `both`) performs the Windows transition, waits for readiness, then starts the Linux display manager on the remaining host GPU or iGPU.

`Linux` (`linux`) requests graceful guest shutdown, waits up to `SHUTDOWN_TIMEOUT`, and returns GPU functions to Linux. It never calls libvirt reattach for a function already owned by a Linux driver. Needed reattach operations are bounded, no selected function may remain on `vfio-pci`, host modules are loaded, GPU health is verified, and only then is the display manager started.

## Safety

- Run GPU transitions from TTY or SSH. Graphical-session execution is rejected unless `ALLOW_GUI=1`.
- Mutating TUI modes acquire sudo on the originating TTY, then a user-owned supervisor invokes `sudo -n` before `setsid` starts the detached privileged worker. The worker therefore needs no later password prompt after USB/terminal handoff. An expired ticket rejects the launch before device ownership changes and selecting the action again reopens authorization.
- Every raw VM disk must exist, remain unmounted on Linux, and not report `offline`. A configured `WIN_DISK` must resolve to one of those attached disks.
- Every selected GPU function must already exist in the VM's persistent PCI hostdev configuration.
- Missing configured USB devices stop VM startup before display-manager shutdown.
- Inconsistent PCI state, including a stale uevent driver without a sysfs driver link, stops immediately with reboot guidance.
- NVIDIA module unload failure is a hard stop before libvirt can partially detach a busy GPU.
- Graceful guest shutdown is the default. The tool never calls `virsh destroy`.
- GPU transitions, bulk and per-device USB mutations, and configuration writes share a non-blocking `flock`; CLI, classic-menu, and TUI operations cannot overlap. A staged USB batch validates every device before changing any, logs each result, and reports partial failure without attempting an unsafe automatic rollback.
- The TUI briefly suspends curses for `sudo -v`, then starts the user-owned supervisor described above. Closing the TUI, terminal, or SSH session does not cancel its detached privileged worker, and there is deliberately no unsafe process-cancel action.

## Runtime Files

Detached actions store one mode-`0600` `.log` and `.meta` pair per action under `$XDG_RUNTIME_DIR/vm-helper/`. The unprivileged supervisor owns and atomically updates those files even when the transition itself runs as root. The metadata uses the same versioned NUL-delimited record protocol as dashboard snapshots, so labels and paths are never reconstructed by splitting display text. A later TUI launch finds a running or most recent action, reconnects to its log, and shows its final outcome even after the worker PID exits.

When `$XDG_RUNTIME_DIR` is unavailable, the helper uses `/run/user/$UID` when present, then a private directory below the system temporary directory. The runtime directory and mutation lock are mode `0700`/`0600`. Runtime records are transient and must not be committed.

## Requirements

Required host commands:

- Bash 5+
- Python 3 with standard-library `curses` for the TUI
- `flock` (normally from util-linux)
- `sudo` for non-root GPU ownership transitions
- `virsh` and a working system libvirt connection
- `xmllint` from libxml2
- `lspci`, `lscpu`, `lsblk`, `fuser`, `udevadm`, `modprobe`, `timeout`, and systemd
- `lsusb` for the full hardware report

Firmware IOMMU support and suitable device isolation are still prerequisites. The Linux desktop should use another GPU or iGPU while the selected device belongs to the VM.

## Validation

```bash
bash -n vm-helper vm-gpu-manager windows4090 linux4090 linux-vm linuxvm usb-vm-helper scripts/*.sh
bash tests/vm-helper-test.sh
python3 -m unittest -v tests/test_tui_unit.py tests/test_tui_pty.py
./vm-helper --help
./vm-helper hardware
./vm-helper status
VM_MANAGER_CONFIG=/nonexistent ./vm-helper status
git diff --check
```

The read-only commands are safe to run from a desktop. GPU and USB ownership commands intentionally mutate machine state and may require sudo. USB attach/detach errors are reported instead of being treated as an assumed already-attached state.

If the screen is garbled, confirm `TERM` matches the client (`linux` on a raw console, commonly `xterm-256color` elsewhere), then use `reset` or the classic menu. `TERM=dumb` intentionally bypasses curses. If a TUI disappears during a transition, relaunch it to reconnect to the newest Activity Log; do not start an opposing direct command while the action lock is active. If launch reports that sudo authorization expired, select the action again to get a fresh prompt. If Validate reports `FAILED`, its output describes a completed read-only check with a failed safety condition, not a transition that is still running.

## Handoff

See `docs/HANDOFF.md` for adaptation and troubleshooting notes.
