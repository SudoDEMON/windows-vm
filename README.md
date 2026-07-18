# Interactive VM Helper

`vm-helper` is one interactive Bash tool for switching a Linux host and a libvirt VM around a passed-through GPU and USB devices. It replaces the former collection of host-specific scripts while retaining their familiar command names as symlinks.

The tool does not create a Windows VM. The libvirt domain must already exist, and its selected GPU PCI functions must be persistent managed host devices. Both raw-block and file-backed VM disks are supported.

## Start

Launch the menu:

```bash
./vm-helper
```

The menu provides status, hardware discovery, configuration, GPU ownership modes, and live USB controls. Direct commands are also available:

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

`check` is a read-only transition preflight. It validates persistent PCI hostdev membership, PCI driver consistency, raw-disk safety, configured USB presence, and vendor GPU health without changing ownership.

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
- Raw block disks are discovered from `virsh domblklist`; file-backed VMs skip raw-disk checks.
- USB IDs come from persistent VM XML or an interactive scan of connected devices.
- USB hostdev XML is generated at runtime, so static per-device XML files are unnecessary.

With exactly one configured VM, read-only status works without `vm-helper.env`:

```bash
VM_MANAGER_CONFIG=/nonexistent ./vm-helper status
```

For multiple VMs or explicit hardware choices, run the interactive wizard:

```bash
./vm-helper configure
```

It writes ignored, mode-`0600` `vm-helper.env` and backs up an existing file before replacement. `vm-helper.env.example` documents every override. The former `REQUIRED_USB` array remains accepted for compatibility.

## Modes

`Windows` (`windows`) validates the VM, selected PCI hostdevs, disk, USB presence, GUI state, and PCI driver consistency. It stops the display manager, checks GPU users, safely unloads the NVIDIA stack when applicable, starts the VM, verifies every GPU function reached `vfio-pci`, and live-attaches configured USB devices. Linux remains at TTY.

`Linux + Windows` (`linux-windows`, also `coexist` or `both`) performs the Windows transition, waits for readiness, then starts the Linux display manager on the remaining host GPU or iGPU.

`Linux` (`linux`) requests graceful guest shutdown, waits up to `SHUTDOWN_TIMEOUT`, and returns GPU functions to Linux. It never calls libvirt reattach for a function already owned by a Linux driver. Needed reattach operations are bounded, host modules are loaded, GPU health is verified, and only then is the display manager started.

## Safety

- Run GPU transitions from TTY or SSH. Graphical-session execution is rejected unless `ALLOW_GUI=1`.
- A raw VM disk must exist, remain unmounted on Linux, and not report `offline`.
- Every selected GPU function must already exist in the VM's persistent PCI hostdev configuration.
- Missing configured USB devices stop VM startup before display-manager shutdown.
- Inconsistent PCI state, including a stale uevent driver without a sysfs driver link, stops immediately with reboot guidance.
- NVIDIA module unload failure is a hard stop before libvirt can partially detach a busy GPU.
- Graceful guest shutdown is the default. The tool never calls `virsh destroy`.

## Requirements

Required host commands:

- Bash 5+
- `virsh` and a working system libvirt connection
- `xmllint` from libxml2
- `lspci`, `lscpu`, `lsblk`, `fuser`, `udevadm`, `modprobe`, `timeout`, and systemd
- `lsusb` for the full hardware report

Firmware IOMMU support and suitable device isolation are still prerequisites. The Linux desktop should use another GPU or iGPU while the selected device belongs to the VM.

## Validation

```bash
bash -n vm-helper vm-gpu-manager windows4090 linux4090 linux-vm linuxvm usb-vm-helper scripts/*.sh
./vm-helper --help
./vm-helper hardware
./vm-helper status
VM_MANAGER_CONFIG=/nonexistent ./vm-helper status
git diff --check
```

The read-only commands are safe to run from a desktop. GPU and USB ownership commands intentionally mutate machine state and may require sudo.

## Handoff

See `docs/HANDOFF.md` for adaptation and troubleshooting notes.
