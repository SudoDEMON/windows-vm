#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VM_MANAGER_CONFIG=/nonexistent source "$ROOT/vm-helper"

fail() {
  printf 'FAIL: %s\n' "$*" >&2
  exit 1
}

assert_equal() {
  [[ "$1" == "$2" ]] || fail "expected '$2', got '$1'"
}

VM=fake-vm
VM_XML='<domain><devices>
  <disk type="block" device="disk"><source dev="/dev/raw-one"/></disk>
  <disk type="file" device="disk"><source file="/tmp/disk.qcow2"/></disk>
  <disk type="block" device="disk"><source dev="/dev/raw-two"/></disk>
  <disk type="block" device="cdrom"><source dev="/dev/sr0"/></disk>
</devices></domain>'

discover_disks_from_vm
assert_equal "${#VM_RAW_DISKS[@]}" 2
assert_equal "${VM_RAW_DISKS[0]}" /dev/raw-one
assert_equal "${VM_RAW_DISKS[1]}" /dev/raw-two

WIN_DISK=/dev/raw-two
validate_configured_disk
WIN_DISK=/dev/not-attached
if (validate_configured_disk >/dev/null 2>&1); then
  fail 'an unattached configured disk was accepted'
fi

virsh() {
  case "$*" in
    *' dominfo missing-vm') return 1 ;;
    *' list --all --name') printf '%s\n' only-domain ;;
    *) return 1 ;;
  esac
}

VM=missing-vm
if (resolve_vm >/dev/null 2>&1); then
  fail 'an invalid explicit VM silently fell back to another domain'
fi

VM=''
resolve_vm >/dev/null
assert_equal "$VM" only-domain
if (required_domstate >/dev/null 2>&1); then
  fail 'an unreadable VM state was treated as a valid state'
fi

menu_failure_probe() {
  false
  printf '%s\n' 'continued after failure'
}
probe_output="$(run_menu_action menu_failure_probe 2>/dev/null)"
assert_equal "$probe_output" ''

printf '%s\n' 'vm-helper regression tests passed'
