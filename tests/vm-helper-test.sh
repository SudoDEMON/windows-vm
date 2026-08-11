#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VM_MANAGER_CONFIG=/nonexistent source "$ROOT/vm-helper"
test_tmp="$(mktemp -d)"
export XDG_RUNTIME_DIR="$test_tmp/runtime"

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

usb_counts_for_test=1
usb_device_count() { printf '%s' "$usb_counts_for_test"; }
usb_label() { printf 'Test USB %s:%s' "$1" "$2"; }
USB_XML_FILE="$test_tmp/device.xml"
usb_xml_file() { printf '<hostdev/>\n' >"$USB_XML_FILE"; }
usb_live_count() {
  [[ "$1:$2" == 5678:ef01 ]] && printf 1 || printf 0
}
route_calls_file="$test_tmp/usb-route-calls"
: >"$route_calls_file"
fail_route_id=''
virsh() {
  case "$*" in
    *' attach-device '*)
      printf 'attach %s\n' "$*" >>"$route_calls_file"
      [[ -z "$fail_route_id" || "$(<"$USB_XML_FILE")" != *"$fail_route_id"* ]]
      ;;
    *' detach-device '*)
      printf 'detach %s\n' "$*" >>"$route_calls_file"
      [[ -z "$fail_route_id" || "$(<"$USB_XML_FILE")" != *"$fail_route_id"* ]]
      ;;
    *) return 1 ;;
  esac
}
udevadm() { :; }
_usb_route --windows 1234:abcd --linux 5678:ef01 >"$test_tmp/usb-route.log"
route_output="$(<"$test_tmp/usb-route.log")"
[[ "$route_output" == *'routed Test USB 1234:abcd (1234:abcd) to Windows'* ]] \
  || fail 'per-device USB route did not attach the Windows target'
[[ "$route_output" == *'routed Test USB 5678:ef01 (5678:ef01) to Linux'* ]] \
  || fail 'per-device USB route did not detach the Linux target'
assert_equal "$(wc -l <"$route_calls_file")" 2
usb_xml_file() { printf '%s:%s\n' "$1" "$2" >"$USB_XML_FILE"; }
: >"$route_calls_file"
fail_route_id=5678:ef01
if (_usb_route --windows 1234:abcd --linux 5678:ef01 >"$test_tmp/usb-route-partial.log" 2>&1); then
  fail 'per-device USB route hid an operational batch failure'
fi
grep -q 'routed Test USB 1234:abcd (1234:abcd) to Windows' "$test_tmp/usb-route-partial.log" \
  || fail 'USB batch did not retain and report its earlier successful route'
grep -q 'earlier changes were retained' "$test_tmp/usb-route-partial.log" \
  || fail 'USB batch failure did not explain its partial result'
fail_route_id=''
usb_counts_for_test=2
if (_usb_route --windows 1234:abcd >/dev/null 2>&1); then
  fail 'per-device USB route accepted an ambiguous duplicate VID:PID'
fi
usb_counts_for_test=1
if (_usb_route --windows invalid >/dev/null 2>&1); then
  fail 'per-device USB route accepted an invalid VID:PID'
fi
if (_usb_route --windows 1d6b:0003 >/dev/null 2>&1); then
  fail 'per-device USB route accepted a Linux root hub'
fi

original_script_path="$SCRIPT_PATH"
supervisor_dir="$(ensure_runtime_dir)"
success_token=supervisor-success
success_log="$supervisor_dir/action-$success_token.log"
: >"$success_log"
: >"$supervisor_dir/action-$success_token.start"
SCRIPT_PATH=/usr/bin/true
SUDO_CALLS=()
worker_supervise "$success_token" linux-windows "$supervisor_dir" "$UID" 100 >/dev/null
assert_equal "${SUDO_CALLS[0]}" "-n setsid /usr/bin/true __worker-run $success_token linux-windows $supervisor_dir $UID"

failure_token=supervisor-failure
failure_log="$supervisor_dir/action-$failure_token.log"
: >"$failure_log"
: >"$supervisor_dir/action-$failure_token.start"
SCRIPT_PATH=/usr/bin/false
SUDO_CALLS=()
if worker_supervise "$failure_token" linux-windows "$supervisor_dir" "$UID" 200 >/dev/null 2>&1; then
  fail 'worker supervisor hid a privileged worker failure'
fi
assert_equal "${SUDO_CALLS[0]}" "-n setsid /usr/bin/false __worker-run $failure_token linux-windows $supervisor_dir $UID"

route_token=supervisor-route
route_log="$supervisor_dir/action-$route_token.log"
: >"$route_log"
: >"$supervisor_dir/action-$route_token.start"
SCRIPT_PATH=/usr/bin/true
SUDO_CALLS=()
worker_supervise "$route_token" usb-route "$supervisor_dir" "$UID" 300 --windows 1234:abcd >/dev/null
assert_equal "${SUDO_CALLS[0]}" \
  "-n setsid /usr/bin/true __worker-run $route_token usb-route $supervisor_dir $UID --windows 1234:abcd"

SCRIPT_PATH=/usr/bin/true
SUDO_CALLS=()
worker_start linux-windows >"$test_tmp/worker-start.bin"
assert_equal "${SUDO_CALLS[0]}" '-n true'
SUDO_CALLS=()
worker_start check >"$test_tmp/check-start.bin"
assert_equal "${#SUDO_CALLS[@]}" 0
if (worker_start usb-route --windows invalid >/dev/null 2>&1); then
  fail 'USB route worker accepted an invalid VID:PID'
fi

PYTHONPATH="$ROOT" python3 - "$supervisor_dir" "$test_tmp/worker-start.bin" "$test_tmp/check-start.bin" <<'PY'
import os
from pathlib import Path
import sys
from vm_helper_tui.protocol import parse_records

directory = Path(sys.argv[1])
success = parse_records((directory / "action-supervisor-success.meta").read_bytes())[0].fields
failure = parse_records((directory / "action-supervisor-failure.meta").read_bytes())[0].fields
route = parse_records((directory / "action-supervisor-route.meta").read_bytes())[0].fields
assert success[3] == "complete" and success[4] == "100" and success[6] == "0"
assert failure[3] == "failed" and failure[4] == "200" and failure[6] == "1"
assert route[2] == "usb-route" and route[3] == "complete" and route[6] == "0"
for path in (directory / "action-supervisor-success.meta", directory / "action-supervisor-failure.meta",
             directory / "action-supervisor-route.meta"):
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.stat().st_uid == os.getuid()
for payload, action in ((Path(sys.argv[2]).read_bytes(), "linux-windows"),
                        (Path(sys.argv[3]).read_bytes(), "check")):
    worker = parse_records(payload)[0]
    assert worker.kind == "worker" and worker.fields[2] == action
PY
SCRIPT_PATH="$original_script_path"
WORKER_RUNTIME_DIR=""
WORKER_OWNER_UID=""

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
