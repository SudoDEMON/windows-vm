#!/usr/bin/env bash
set -euo pipefail

VM="${1:-win11-basic}"
URI="qemu:///system"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -f "$ROOT/vm-helper.env" ]]; then
  source "$ROOT/vm-helper.env"
fi
VM="${1:-${VM:-win11-basic}}"
URI="${URI:-qemu:///system}"

echo "--- VM USB devices ---"
virsh -c "$URI" qemu-monitor-command "$VM" --hmp 'info usb' 2>/dev/null || true

echo "--- Linux input devices ---"
awk 'BEGIN{RS=""} /Wooting|Logitech|G305|SteelSeries|Arctis|Nova|SINO|RK Bluetooth/ {print $0 "\n"}' /proc/bus/input/devices
