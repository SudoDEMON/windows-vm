# Handoff Notes

This repository is a working helper set for a Linux host that can either use an RTX 4090 locally or pass it through to a Windows 11 libvirt VM.

The scripts are intentionally conservative and host-specific. They are a good starting point for a similar machine, but another host should review PCI IDs, USB IDs, display layout, VM name, and raw disk path before use.

For Claude users: `CLAUDE.md` contains agent-facing safety rules. For Codex or other agents, `AGENTS.md` contains the same repo-specific operating constraints.

## Modes

- `./windows4090`: start the Windows VM with the RTX 4090 passed through and leave Linux at TTY.
- `./linux-vm`: start or verify the Windows VM with the RTX 4090 passed through, then start the Linux display manager on the iGPU.
- `./linux4090`: gracefully shut down the Windows VM, reattach the RTX 4090 to Linux, and start the Linux display manager.
- `./usb-vm-helper`: show or live-toggle configured USB passthrough devices.

## Local Configuration

The scripts have defaults for the original machine, but they will source `vm-helper.env` from the repo root when present. Keep that file untracked.

Start from:

```bash
cp vm-helper.env.example vm-helper.env
```

Then edit values for the target host.

Important values:

- `VM`: libvirt domain name.
- `URI`: libvirt URI.
- `WIN_DISK`: stable `/dev/disk/by-id/...` path for the Windows raw disk.
- `REQUIRED_USB`: bash array of `Label|vendor|product` entries that must be present before starting the VM.
- `GPU_PCI`: bash array of PCI addresses for the GPU and its audio function.
- `GPU_NODE`: libvirt node-device names for host reattach.

## Hardware Expectations

The original setup uses:

- Linux display on the CPU/iGPU.
- Windows display on the RTX 4090 physical output.
- Windows raw disk on an external USB-C NVMe.
- Windows USB defaults:
  - Wooting 60HE: `31e3:1312`
  - Logitech G305 receiver: `046d:c53f`
  - SteelSeries Nova Pro Wireless: `1038:12e5`

The external NVMe path matters. If Linux reports the disk as `offline`, do not start the VM. Power-cycle or replug the enclosure first.

## Preconditions

The host needs:

- IOMMU/SVM enabled in firmware.
- iGPU enabled and usable by Linux.
- RTX 4090 and NVIDIA audio in an isolated IOMMU group.
- `qemu`, `libvirt`, OVMF, TPM support, and `virsh`.
- A Windows VM already defined in libvirt.
- The Linux display manager stopped before handing the 4090 to Windows.

The original host boots to TTY by default:

```bash
sudo systemctl set-default multi-user.target
```

To restore graphical boot:

```bash
sudo systemctl set-default graphical.target
```

## Validation Commands

Before starting Windows:

```bash
lsblk -o NAME,MODEL,TRAN,SIZE,STATE,MOUNTPOINTS
virsh -c qemu:///system domstate "$VM"
lspci -nnk -s 01:00.0
lspci -nnk -s 01:00.1
```

While Windows owns the GPU:

```bash
lspci -nnk -s 01:00.0
lspci -nnk -s 01:00.1
virsh -c qemu:///system qemu-monitor-command "$VM" --hmp 'info usb'
```

Expected GPU driver while Windows owns it:

```text
vfio-pci
```

Expected GPU driver after `linux4090` returns it to Linux:

```text
nvidia
```
