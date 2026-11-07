#!/usr/bin/env bash
# NitroSense Linux installer.
#   sudo bash install.sh                 install / update
#   sudo bash install.sh --predator-v4   also set "options acer_wmi predator_v4=1"
#                                        (only if platform_profile is missing, see README)
set -euo pipefail

usage() { sed -n '2,5p' "$0"; }

if [[ ${EUID} -ne 0 ]]; then
    echo "À lancer en root : sudo bash install.sh   (ou : pkexec bash \"$PWD/install.sh\")" >&2
    exit 1
fi

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PREFIX=/opt/nitrosense-linux
# The desktop user: sudo sets SUDO_USER, pkexec sets PKEXEC_UID.
TARGET_USER="${SUDO_USER:-}"
if [[ -z "${TARGET_USER}" && -n "${PKEXEC_UID:-}" ]]; then
    TARGET_USER="$(id -nu "${PKEXEC_UID}" 2>/dev/null || true)"
fi
PREDATOR_V4=0
for arg in "$@"; do
    case "$arg" in
        --predator-v4) PREDATOR_V4=1 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Option inconnue : $arg" >&2; usage; exit 1 ;;
    esac
done

echo "==> Vérifications"
command -v python3 >/dev/null || { echo "python3 est requis" >&2; exit 1; }
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' \
    || { echo "Python 3.9 ou plus récent requis" >&2; exit 1; }
if ! python3 -c 'import PyQt6.QtWidgets' 2>/dev/null && ! python3 -c 'import PySide6.QtWidgets' 2>/dev/null; then
    echo "ATTENTION : PyQt6 absent, l'interface ne démarrera pas (le service et la CLI fonctionnent)."
    echo "            Debian/Ubuntu : sudo apt install python3-pyqt6 | Fedora : sudo dnf install python3-pyqt6"
    echo "            Arch : sudo pacman -S python-pyqt6"
fi
if ! modinfo acpi_call >/dev/null 2>&1; then
    echo "ATTENTION : module acpi_call introuvable. Sans lui : pas de mode Perso/Max des ventilateurs,"
    echo "            pas d'éclairage clavier ni de limite batterie WMI (voir README, section acpi_call)."
fi

echo "==> Copie du programme dans ${PREFIX}"
rm -rf "${PREFIX}"
install -d "${PREFIX}"
cp -r "${SRC}/nitrosense" "${PREFIX}/"
find "${PREFIX}" -name '__pycache__' -prune -exec rm -rf {} +
find "${PREFIX}" -type d -exec chmod 755 {} +
find "${PREFIX}" -type f -exec chmod 644 {} +

echo "==> Lanceurs /usr/local/bin/nitrosense et nitrosense-cli"
for pair in "nitrosense:nitrosense.gui" "nitrosense-cli:nitrosense.cli"; do
    name="${pair%%:*}"
    module="${pair#*:}"
    cat > "/usr/local/bin/${name}" <<EOF
#!/bin/sh
export PYTHONPATH="${PREFIX}\${PYTHONPATH:+:\$PYTHONPATH}"
exec python3 -m ${module} "\$@"
EOF
    chmod 755 "/usr/local/bin/${name}"
done

echo "==> Configuration /etc/nitrosense/config.json"
install -d -m 755 /etc/nitrosense
if [[ ! -f /etc/nitrosense/config.json ]]; then
    python3 - "${TARGET_USER}" > /etc/nitrosense/config.json <<'EOF'
import json, sys
user = sys.argv[1]
print(json.dumps({
    "allowed_users": [user] if user and user != "root" else [],
    "fan_backend": "auto",
    "kb_hotkey_number": 132,
    "allow_unknown_model": False,
    "performance_requires_ac": True,
    "min_battery_for_performance": 40,
    "thermal_guard": {"enabled": True, "cpu_trip": 92, "gpu_trip": 87, "cpu_clear": 80, "gpu_clear": 75},
}, indent=2))
EOF
    echo "    utilisateur autorisé : ${TARGET_USER:-(administrateurs sudo/wheel)}"
else
    echo "    conservée (déjà présente)"
fi

echo "==> Modules noyau"
install -d /etc/modules-load.d
echo "acpi_call" > /etc/modules-load.d/nitrosense-linux.conf
modprobe acpi_call 2>/dev/null && echo "    acpi_call chargé" || echo "    acpi_call non chargé"
if [[ ${PREDATOR_V4} -eq 1 ]]; then
    echo "options acer_wmi predator_v4=1" > /etc/modprobe.d/nitrosense-linux-acer-wmi.conf
    echo "    acer_wmi predator_v4=1 activé (rechargement du module...)"
    modprobe -r acer_wmi 2>/dev/null && modprobe acer_wmi || echo "    -> redémarrer pour l'appliquer"
fi

echo "==> Service systemd nitrosensed"
install -m 0644 "${SRC}/packaging/nitrosensed.service" /etc/systemd/system/nitrosensed.service
SLEEP_DIR="$(pkg-config --variable=systemdsleepdir systemd 2>/dev/null || true)"
SLEEP_DIR="${SLEEP_DIR:-/usr/lib/systemd/system-sleep}"
install -d "${SLEEP_DIR}"
install -m 0755 "${SRC}/packaging/system-sleep-nitrosense" "${SLEEP_DIR}/nitrosense-linux"
systemctl daemon-reload
systemctl enable nitrosensed.service >/dev/null
systemctl restart nitrosensed.service

echo "==> Menu des applications"
install -Dm 0644 "${SRC}/packaging/nitrosense-linux.desktop" /usr/share/applications/nitrosense-linux.desktop
install -Dm 0644 "${SRC}/packaging/nitrosense-linux.svg" /usr/share/icons/hicolor/scalable/apps/nitrosense-linux.svg
gtk-update-icon-cache -q /usr/share/icons/hicolor 2>/dev/null || true
update-desktop-database -q /usr/share/applications 2>/dev/null || true

echo "==> Terminé"
sleep 2
if ! /usr/local/bin/nitrosense-cli status; then
    echo "Le service ne répond pas, journal :"
    journalctl -u nitrosensed -n 40 --no-pager || true
fi
echo
echo "Lancer l'interface : nitrosense   (ou « NitroSense Linux » dans le menu)"
