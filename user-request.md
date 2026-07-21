# Unified Interactive VM Manager

## Request

- Combine all functionality from the project scripts into one interactive shell script.
- Discover GPU, CPU, USB, storage, VM, and related host state dynamically so the tool can be adapted to another computer without source edits.

## Acceptance Criteria

- [x] One regular executable, `vm-helper`, owns all behavior.
- [x] Running `vm-helper` without arguments opens an interactive menu.
- [x] The first three menu choices use the short labels `Linux`, `Windows`, and `Linux + Windows`.
- [x] The menu supports Up/Down navigation with Enter as well as direct number selection.
- [x] `~/vm-helper` resolves to the repository implementation.
- [x] Direct CLI commands cover Windows-only, Windows plus Linux desktop, return-to-Linux, USB status/attach/detach, status, preflight, hardware discovery, and configuration.
- [x] Legacy command names remain usable as symlinks into the unified implementation.
- [x] VM names are discovered from libvirt and a sole domain can be selected without local configuration.
- [x] GPU and companion PCI functions are discovered from persistent VM XML or selected interactively from live display-class devices and IOMMU groups.
- [x] NVIDIA, AMD, and Intel host module choices are inferred from the selected GPU vendor/driver.
- [x] CPU model/topology and current VM vCPU pinning are read dynamically.
- [x] Every raw VM block disk is discovered from inactive XML and checked; a configured disk must match an attached disk, and file-backed VMs need no raw-disk setting.
- [x] USB devices are discovered from VM XML or selected from connected hardware; hostdev XML is generated at runtime.
- [x] Existing GPU transition safeguards, graceful shutdown, and stale-driver detection remain present.
- [x] Documentation and the example configuration describe the unified dynamic workflow.

## Implementation Evidence

- `vm-helper` is the only regular executable containing transition logic; `vm-gpu-manager` is a compatibility symlink.
- `windows4090`, `linux4090`, `linux-vm`, `linuxvm`, `usb-vm-helper`, and the former `scripts/win-input-*.sh` paths are compatibility symlinks.
- Static per-device USB XML files were removed.
- Read-only zero-config `status` and USB discovery were tested against the live libvirt domain.
- The configuration wizard reports disks already attached to the VM rather than implying that selecting an arbitrary host disk edits libvirt XML.
- Source-level regression tests cover multi-disk discovery, attached-disk validation, explicit-VM selection, unreadable-state safety, and interactive error isolation without changing live machine state.
- The interactive wizard was completed in a PTY against a temporary config, and the generated config passed `status` and `check`.
- Bash syntax and `git diff --check` are part of the final validation gate.
