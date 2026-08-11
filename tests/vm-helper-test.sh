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

SUDO_CALLS=()
sudo() {
  SUDO_CALLS+=("$*")
  [[ "${1:-}" == -n ]] && shift
  [[ "${1:-}" == -v ]] && return 0
  "$@"
}

root_probe_ran=0
root_probe() { root_probe_ran=1; }
begin_root_session
first_keepalive_pid="$SUDO_KEEPALIVE_PID"
[[ -n "$first_keepalive_pid" ]] || fail 'sudo keepalive did not start'
kill -0 "$first_keepalive_pid" 2>/dev/null || fail 'sudo keepalive exited unexpectedly'
begin_root_session
assert_equal "$SUDO_KEEPALIVE_PID" "$first_keepalive_pid"
as_root root_probe
assert_equal "$root_probe_ran" 1
assert_equal "${SUDO_CALLS[0]}" '-n true'
assert_equal "${SUDO_CALLS[-1]}" '-n root_probe'
cleanup
assert_equal "$SUDO_KEEPALIVE_PID" ''

load_runtime_config() { :; }
required_domstate() { printf running; }
wait_for_vfio() { return 0; }
driver_for() { printf vfio-pci; }
sleep() { fail 'already-running coexist mode unexpectedly slept'; }
begin_root_session_calls=0
begin_root_session() { ((begin_root_session_calls += 1)); }
display_manager_active=1
systemctl() {
  case "$1" in
    is-active) ((display_manager_active == 1)) ;;
    start) display_manager_active=1 ;;
    *) return 1 ;;
  esac
}
GPU_PCI=(0000:01:00.0 0000:01:00.1)
start_coexist_mode >/dev/null
assert_equal "$begin_root_session_calls" 0

display_manager_active=0
as_root() { "$@"; }
start_coexist_mode >/dev/null
assert_equal "$begin_root_session_calls" 1
assert_equal "$display_manager_active" 1

test_tmp="$(mktemp -d)"
protocol_file="$test_tmp/protocol.bin"
{
  protocol_begin
  protocol_record sample 'label | with spaces' '/dev/a path' $'line one\nline two'
} >"$protocol_file"
PYTHONPATH="$ROOT" python3 - "$protocol_file" <<'PY'
import pathlib
import sys
from vm_helper_tui.protocol import parse_records

records = parse_records(pathlib.Path(sys.argv[1]).read_bytes())
assert records[0].kind == "sample"
assert records[0].fields == ("label | with spaces", "/dev/a path", "line one\nline two")
PY

if (internal_configure --vm fake --gpu invalid >/dev/null 2>&1); then
  fail 'internal configuration accepted an invalid GPU BDF'
fi

status_report() { printf '%s' direct-status-dispatch; }
assert_equal "$(dispatch status)" direct-status-dispatch

export XDG_RUNTIME_DIR="$test_tmp/runtime"
ready_file="$test_tmp/lock-ready"
(with_mutation_lock bash -c ': >"$1"; sleep 1' _ "$ready_file") &
lock_holder_pid=$!
while [[ ! -e "$ready_file" ]]; do command sleep 0.02; done
if (start_windows_mode >/dev/null 2>&1); then
  fail 'GPU transition ignored an active mutation lock'
fi
if (usb_action attach >/dev/null 2>&1); then
  fail 'USB mutation ignored an active mutation lock'
fi
_write_config() { return 0; }
if (write_config >/dev/null 2>&1); then
  fail 'configuration write ignored an active mutation lock'
fi
wait "$lock_holder_pid"

start_windows_mode() { printf windows; }
start_coexist_mode() { printf coexist; }
return_linux_mode() { printf linux; }
preflight_report() { printf check; }
hardware_report() { printf hardware; }
configure_interactive() { printf configure; }
usb_action() { printf 'usb:%s' "$1"; }
tui_available() { return 1; }
assert_equal "$(dispatch windows)" windows
assert_equal "$(dispatch linux-windows)" coexist
assert_equal "$(dispatch linux)" linux
assert_equal "$(dispatch check)" check
assert_equal "$(dispatch hardware)" hardware
assert_equal "$(dispatch configure 2>/dev/null)" configure
assert_equal "$(dispatch usb status)" usb:status
rm -rf -- "$test_tmp"

printf '%s\n' 'vm-helper regression tests passed'
