#!/usr/bin/env bash
# NitroSense Linux uninstaller.
#   sudo bash uninstall.sh           remove the program (keeps /etc/nitrosense and saved settings)
#   sudo bash uninstall.sh --purge   also remove the configuration and the saved state
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
    echo "À lancer avec sudo : sudo bash uninstall.sh" >&2
    exit 1
fi
PURGE=0
[[ "${1:-}" == "--purge" ]] && PURGE=1

# Stopping the service puts the fans back to Auto.
systemctl disable --now nitrosensed.service 2>/dev/null || true
rm -f /etc/systemd/system/nitrosensed.service
SLEEP_DIR="$(pkg-config --variable=systemdsleepdir systemd 2>/dev/null || true)"
rm -f "${SLEEP_DIR:-/usr/lib/systemd/system-sleep}/nitrosense-linux" /usr/lib/systemd/system-sleep/nitrosense-linux
systemctl daemon-reload
rm -rf /opt/nitrosense-linux
rm -f /usr/local/bin/nitrosense /usr/local/bin/nitrosense-cli
rm -f /usr/share/applications/nitrosense-linux.desktop
rm -f /usr/share/icons/hicolor/scalable/apps/nitrosense-linux.svg
rm -f /etc/modules-load.d/nitrosense-linux.conf /etc/modprobe.d/nitrosense-linux-acer-wmi.conf
if [[ ${PURGE} -eq 1 ]]; then
    rm -rf /etc/nitrosense /var/lib/nitrosense
fi
echo "NitroSense Linux désinstallé.$([[ ${PURGE} -eq 1 ]] && echo ' Configuration supprimée.')"
