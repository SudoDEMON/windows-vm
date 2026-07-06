# Windows VM 4090 Helpers

Bash helpers for switching a Linux host and a Windows libvirt VM between RTX 4090 ownership modes.

This repository is intentionally small and script-focused. It does not include VM images, disk snapshots, VirtIO ISOs, OVMF variables, TPM state, diagnostic logs, or local machine evidence.

## Modes

```bash
./windows4090     # Start the Windows VM with the 4090; keep Linux at TTY
./linux-vm        # Start/verify the Windows VM with the 4090, then start Linux display-manager on iGPU
./linux4090       # Shut down the VM, return the 4090 to Linux, start display-manager
./usb-vm-helper   # Inspect or live-toggle configured USB passthrough devices
```

`linuxvm` is a compatibility alias for `linux-vm`.

## Required Local Config

Copy the example config and edit it for the target machine:

```bash
cp vm-helper.env.example vm-helper.env
```

`vm-helper.env` is intentionally ignored by git because it should contain machine-local values such as:

- libvirt VM name
- raw Windows disk `/dev/disk/by-id/...` path
- GPU PCI function addresses
- libvirt node-device names
- USB vendor/product IDs expected for passthrough

At minimum, set `WIN_DISK` to a stable `/dev/disk/by-id/...` path. Do not use `/dev/sdX` as a long-term config value because it can change across boots.

## Default USB Set

The example defaults expect these Windows USB passthrough devices:

- Wooting 60HE keyboard: `31e3:1312`
- Logitech G305 receiver: `046d:c53f`
- SteelSeries Nova Pro Wireless: `1038:12e5`

Adjust `REQUIRED_USB` in `vm-helper.env` for a different setup.

## Quick Start

From TTY or SSH:

```bash
./windows4090
```

To run the Windows VM and then start the Linux desktop on the iGPU:

```bash
./linux-vm
```

To cleanly return the machine to Linux owning the 4090:

```bash
./linux4090
```

USB passthrough helpers:

```bash
./usb-vm-helper status
./usb-vm-helper to-vm
./usb-vm-helper to-linux
```

## Host Assumptions

The intended architecture is:

- Linux desktop on the CPU/iGPU.
- Windows VM display on the passed-through RTX 4090 physical output.
- The Windows VM already exists in libvirt.
- The 4090 GPU and its audio function are isolated enough for passthrough.
- The Windows system disk is exposed to QEMU as a raw block device.

The host can boot to TTY by default:

```bash
sudo systemctl set-default multi-user.target
```

Restore graphical boot:

```bash
sudo systemctl set-default graphical.target
```

## Safety Checks

`windows4090` refuses to start if:

- `WIN_DISK` is not configured.
- the raw Windows disk is missing.
- the raw Windows disk is mounted by Linux.
- the raw Windows disk reports `offline`.
- required USB devices are missing.
- a graphical session is detected, unless `ALLOW_GUI=1`.
- NVIDIA still has active users after stopping `display-manager`.

`linux4090` requests graceful VM shutdown and waits before returning the 4090 to Linux.

## Validation

Syntax check:

```bash
bash -n windows4090 linux4090 linux-vm linuxvm usb-vm-helper scripts/*.sh
```

Inspect VM/GPU state:

```bash
virsh -c qemu:///system list --all
lspci -nnk -s 01:00.0
lspci -nnk -s 01:00.1
```

Expected GPU driver while Windows owns it:

```text
vfio-pci
```

Expected GPU driver after returning it to Linux:

```text
nvidia
```

## Handoff

For another machine or another user, start with:

- `docs/HANDOFF.md`
- `vm-helper.env.example`
- `CLAUDE.md` or `AGENTS.md`
