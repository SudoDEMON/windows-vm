# Claude Instructions

This repository contains host-specific Bash helpers for a Linux + libvirt Windows VM setup with RTX 4090 PCI passthrough.

## Read First

1. `README.md`
2. `docs/HANDOFF.md`
3. `AGENTS.md`

The scripts are operational tools, not generic library code. They may start or stop `display-manager`, start or shut down a libvirt VM, and detach or reattach PCI/USB devices.

## Important Safety Rules

- Do not run `windows4090`, `linux-vm`, `linux4090`, or `usb-vm-helper to-vm/to-linux` unless the user is intentionally changing VM/device state.
- Do not use `virsh destroy` unless the user explicitly accepts a hard VM stop or the VM is already unrecoverable.
- Do not commit `vm-helper.env`, `downloads/`, `evidence/`, `project-memory.md`, `.shared-agent-context-include`, VM images, ISOs, logs, OVMF vars, TPM state, or other machine-local runtime files.
- If adapting this repo to another host, update `vm-helper.env` locally. Commit only `vm-helper.env.example` when documenting new configurable values.
- Use `/dev/disk/by-id/...` for raw disks. Do not rely on `/dev/sdX` in committed defaults for a different machine.

## Expected Workflow

For edits:

```bash
bash -n windows4090 linux4090 linux-vm linuxvm usb-vm-helper scripts/*.sh
git status --short --ignored
```

For host inspection, prefer read-only commands first:

```bash
virsh -c qemu:///system list --all
virsh -c qemu:///system dumpxml --inactive win11-basic
lspci -nnk -s 01:00.0
lspci -nnk -s 01:00.1
lsblk -o NAME,MODEL,TRAN,SIZE,STATE,MOUNTPOINTS
```

Before starting the Windows VM, the raw Windows disk must be present, unmounted by Linux, and not `offline`.

Before returning to Linux desktop mode, prefer `linux4090` so the Windows VM can shut down cleanly and the GPU can reattach to Linux.
