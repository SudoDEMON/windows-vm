#!/usr/bin/env bash
set -euo pipefail

VM="${1:-win11-basic}"
SCRIPT_PATH="$(readlink -f "${BASH_SOURCE[0]}")"
ROOT="$(cd "$(dirname "$SCRIPT_PATH")/.." && pwd)"
URI="qemu:///system"
if [[ -f "$ROOT/vm-helper.env" ]]; then
  source "$ROOT/vm-helper.env"
fi
VM="${1:-${VM:-win11-basic}}"
URI="${URI:-qemu:///system}"

state="$(virsh -c "$URI" domstate "$VM" 2>/dev/null || true)"
if [[ "$state" != "running" ]]; then
  echo "$VM is not running; start the VM before attaching input."
  exit 1
fi

for device in \
  "$ROOT/devices/wooting-60he-usb.xml" \
  "$ROOT/devices/g305-usb-receiver.xml" \
  "$ROOT/devices/nova-pro-wireless-usb.xml"
do
  name="$(basename "$device" .xml)"
  if virsh -c "$URI" attach-device "$VM" "$device" --live >/dev/null 2>&1; then
    echo "attached $name to $VM"
  else
    echo "$name may already be attached to $VM"
  fi
done

virsh -c "$URI" qemu-monitor-command "$VM" --hmp 'info usb' 2>/dev/null || true
