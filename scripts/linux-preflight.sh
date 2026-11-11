#!/usr/bin/env bash
set -euo pipefail
[[ $(uname -s) == Linux ]] || { echo 'Linux execution host required'; exit 1; }
for binary in python3 systemctl adb fastboot; do
  command -v "$binary" >/dev/null || { echo "Missing: $binary"; exit 1; }
done
python3 --version
adb version
fastboot --version
systemd-analyze verify deploy/systemd/kraken-node.service
echo 'Tool presence only. USB permissions, hub capacity, actual execution and cancellation still require hardware validation.'
