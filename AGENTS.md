# Repository Agent Instructions

This repo contains host-specific helper scripts for switching a Linux desktop and a Windows libvirt VM around a passed-through RTX 4090.

## Start Here

- Read `README.md` first, especially the top "Windows VM 4090 Helpers" section.
- Read `docs/HANDOFF.md` before adapting this setup for another host.
- Treat `windows4090`, `linux4090`, `linux-vm`, and `usb-vm-helper` as machine-level operations: they can stop/start `display-manager`, start/shutdown a VM, and detach/reattach PCI or USB devices.

## Safety

- Do not commit local runtime artifacts: `downloads/`, `evidence/`, `project-memory.md`, `.shared-agent-context-include`, `vm-helper.env`, VM images, ISOs, OVMF vars, TPM state, logs, or generated dumps.
- Keep `vm-helper.env` local. Commit changes to `vm-helper.env.example` when documenting configurable values.
- Prefer graceful VM shutdown through `linux4090`. Use `virsh destroy` only when the user explicitly accepts a hard stop or the guest is already unrecoverable.
- Before starting the Windows VM, confirm the raw Windows disk is present, not mounted by Linux, and not `offline`.
- Before handing the 4090 to Windows, confirm the Linux desktop/display manager is stopped or otherwise not using NVIDIA.

## Validation

Run at least:

```bash
bash -n windows4090 linux4090 linux-vm linuxvm usb-vm-helper scripts/*.sh
git status --short --ignored
```

When changing persistent libvirt XML snippets, inspect:

```bash
virsh -c qemu:///system dumpxml --inactive win11-basic
```

Use HTTPS GitHub remotes for agent-managed pushes.
