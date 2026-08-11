# Handoff Notes

This repository has one safety-critical behavioral source: the Bash `vm-helper` backend. The former `vm-gpu-manager` name and all legacy commands are symlinks into it. The standard-library `vm_helper_tui` package renders and collects telemetry around versioned backend records; it must not grow independent device-transition logic.

## First Run On Another Host

1. Enable IOMMU support in firmware and confirm the intended GPU has usable isolation.
2. Install libvirt/QEMU, `virsh`, `xmllint`, PCI/USB utilities, and the host GPU driver.
3. Define the VM and add the GPU functions as persistent managed PCI host devices.
4. Run `./vm-helper hardware` to inspect CPU, GPUs, drivers, IOMMU groups, disks, and USB.
5. Run `./vm-helper configure` to select the VM, GPU, and USB devices and review companions, persistence, pinning, timers, and attached raw disks.
6. Review `./vm-helper status` before the first transition.

When libvirt has exactly one domain with display-class PCI hostdevs, the tool can derive VM, GPU functions, USB hostdevs, and raw disk directly from inactive XML without a config file.

## Configuration Contract

The local `vm-helper.env` is Bash syntax and intentionally ignored. The wizard creates it with mode `0600` and backs up an existing copy. Supported values are:

- `VM`, `URI`: libvirt domain and connection.
- `WIN_DISK`: optional stable expected raw block path; when set, it must resolve to a raw disk already attached in inactive VM XML. Every attached raw disk is checked regardless.
- `GPU_PCI`: selected GPU/companion PCI functions.
- `GPU_MODULES`: modules needed when returning the GPU to Linux.
- `USB_DEVICES`: `Label|vendor|product` entries.
- `USB_AUTO_DISCOVER`: discover USB hostdevs from inactive VM XML when the array is empty.
- transition timing and `ALLOW_GUI` values documented in `vm-helper.env.example`.

Legacy `REQUIRED_USB` is mapped to `USB_DEVICES`. `GPU_NODE` is no longer needed because libvirt node names are derived from PCI addresses.

The validated internal configuration endpoint accepts exactly one VM, one display-class GPU BDF, and repeated connected USB VID:PID values. It derives same-slot IOMMU companions and host modules itself, then uses the same locked, backed-up mode-`0600` writer as the classic wizard.

## TUI Architecture

- `vm_helper_tui/app.py`: refresh loop, confirmations, sudo handoff, detached actions, and wizard orchestration.
- `vm_helper_tui/state.py`: panel registry/default layout profile, responsive thresholds, input/mouse mapping, and wizard state.
- `vm_helper_tui/views.py`: curses rendering with ASCII borders and adaptive color pairs.
- `vm_helper_tui/protocol.py`: strict `VMH1` NUL-record parser, backend calls, and action/log reconnection.
- `vm_helper_tui/telemetry.py`: `/proc` host collection, counter deltas, mode derivation, and 60-sample sparklines.

`vm-helper machine snapshot` and `machine inventory [VM]` emit a `VMH1\0` header followed by typed records, decimal field counts, and NUL-terminated fields. Never replace this with parsing the human `status` or `hardware` output. Additive record types are compatible; changing field meaning requires a protocol version bump and parser tests.

The panel registry and default profile intentionally make layout order/visibility data-driven, but v1 exposes no customization UI or persistent layout file.

## Operational Invariants

- A selected GPU PCI function must exist both on the host and as a persistent VM PCI hostdev.
- Every attached raw block disk must be online and completely unmounted on Linux.
- Configured USB devices must be connected before VM startup.
- The display manager is stopped before handing the GPU to the VM.
- Sudo authorization is acquired and kept alive before device transitions; post-handoff privileged calls never prompt for input.
- Active GPU users and busy NVIDIA modules stop the transition.
- Post-start verification requires every selected PCI function on `vfio-pci`.
- Return-to-Linux skips libvirt reattach for devices already on a host driver. This prevents the fresh-boot reattach bug that can leave NVIDIA half-detached.
- Return-to-Linux refuses to start the display manager while any selected function remains on `vfio-pci`.
- The display manager starts only after the returned GPU has a host driver and vendor health checks pass.
- Guest shutdown remains graceful; no helper uses `virsh destroy`.
- All GPU, USB, and configuration mutations enter the same `flock`, including nested coexist/USB paths.
- TUI workers are detached session leaders. Per-action binary metadata and text logs live below `$XDG_RUNTIME_DIR/vm-helper/` with mode `0600`; closing curses must never signal or cancel them.

## Vendor Behavior

NVIDIA module handling is explicit because its DRM/UVM/modeset stack must be unloaded in dependency order before passthrough and reloaded in dependency order afterward. AMD and Intel host modules are inferred for return-to-Linux, but they are not globally unloaded before VM start because the same module may also own the host iGPU. For those vendors, libvirt performs per-device detach and reports a busy device normally.

## Troubleshooting

Use the read-only report first:

```bash
./vm-helper status
./vm-helper hardware
```

If PCI `uevent` names a driver but the device has no `driver` symlink, the device is partially transitioned. Reboot before another ownership command.

If an NVIDIA module cannot reload after a rolling-release update, compare the running kernel with `modinfo -F vermagic nvidia`; the tool prints these diagnostics on failure. A reboot into the matching kernel/module set is the normal recovery.

If VM startup reports a selected PCI function is not persistent, add that function to the inactive libvirt domain as a managed PCI hostdev before retrying.

If USB attachment is ambiguous because multiple identical VID:PID devices are connected, use persistent VM XML with explicit USB source addressing or extend the local configuration model before relying on live VID:PID attachment.

If the TUI cannot initialize, use `classic-menu`, `configure-classic`, or `VM_HELPER_TUI=0`. `TERM=dumb` bypasses curses automatically. A raw Linux console should use `TERM=linux`; mouse reporting is optional there.

If a transition outlives its terminal, relaunch the dashboard to reconnect to the newest active action. A stale metadata record is not treated as an active lock: the Bash `flock` remains the authority. Never add force-kill UI for transition workers.

## Validation Additions

`tests/vm-helper-test.sh` covers binary protocol framing, internal argument validation, shared mutation locking, and direct-command dispatch. `tests/test_tui_unit.py` covers parsing, deltas, sparklines, modes, layouts, input/mouse mapping, wizard state, and detached-log reconnection. `tests/test_tui_pty.py` exercises `TERM=linux` at 80x24, `xterm-256color` at 140x40, resize/redraw/navigation, classic fallback, clean exit, and terminal restoration. `tests/fake-vm-helper` never touches real devices.

## Repository Hygiene

Do not commit `vm-helper.env` or its backups, evidence, VM images, ISOs, OVMF variables, TPM state, logs, project memory, project-specific agent instructions, or authentication/session data. The public repository should contain only the generic tool, symlink entrypoints, example config, and documentation.
