#!/usr/bin/env bash
# Read-only hardware probe for NitroSense Linux. Changes nothing.
# Run it with sudo for the complete report:  sudo bash tools/linux_probe.sh
GUIDS="7A4DDFE7-5B5D-40B4-8595-4408E0CC7F56 61EF69EA-865C-4BC3-A502-A0DEBA0CB531 79772EC5-04B1-4BFD-843C-61E7F77B6CC9"

section() { printf '\n===== %s =====\n' "$1"; }

section "Système"
uname -a
grep -E '^(PRETTY_NAME|ID|VERSION_ID)=' /etc/os-release 2>/dev/null
echo "Session: ${XDG_SESSION_TYPE:-?} / ${XDG_CURRENT_DESKTOP:-?}"

section "DMI"
for f in sys_vendor product_name product_family bios_version board_name; do
    printf '%-15s %s\n' "$f" "$(cat /sys/class/dmi/id/$f 2>/dev/null)"
done

section "Secure Boot"
mokutil --sb-state 2>/dev/null || echo "mokutil absent"

section "Modules chargés (acer / wmi / acpi_call / nvidia / temp)"
lsmod | grep -E '^(acer|wmi|acpi_call|nvidia|coretemp|linuwu|facer|platform_profile)' || echo "(aucun)"

section "acer_wmi : paramètres"
modinfo -p acer_wmi 2>/dev/null || echo "modinfo acer_wmi indisponible"
for p in /sys/module/acer_wmi/parameters/*; do [ -e "$p" ] && echo "$(basename "$p") = $(cat "$p" 2>/dev/null)"; done

section "platform_profile"
for f in /sys/firmware/acpi/platform_profile /sys/firmware/acpi/platform_profile_choices; do
    printf '%s: %s\n' "$f" "$(cat "$f" 2>/dev/null || echo absent)"
done
for d in /sys/class/platform-profile/*; do
    [ -d "$d" ] && echo "$d: name=$(cat "$d/name") profile=$(cat "$d/profile") choices=$(cat "$d/choices")"
done

section "hwmon"
for d in /sys/class/hwmon/hwmon*; do
    echo "$d ($(cat "$d/name" 2>/dev/null)): $(ls "$d" | grep -E '^(fan|temp|pwm)' | tr '\n' ' ')"
done
for d in /sys/class/hwmon/hwmon*; do
    if [ "$(cat "$d/name" 2>/dev/null)" = "acer" ]; then
        echo "--- valeurs acer ($d)"
        grep -H . "$d"/fan* "$d"/temp* "$d"/pwm* 2>/dev/null
    fi
done

section "WMI Acer (GUID -> méthode ACPI)"
for g in $GUIDS; do
    found=0
    for d in /sys/bus/wmi/devices/${g}*; do
        [ -e "$d" ] || continue
        found=1
        real=$(readlink -f "$d")
        oid=$(cat "$d/object_id" 2>/dev/null)
        drv=$(basename "$(readlink -f "$d/driver" 2>/dev/null)" 2>/dev/null)
        acpi=""
        p="$real"
        while [ "$p" != "/" ]; do
            if [ -f "$p/firmware_node/path" ]; then acpi=$(cat "$p/firmware_node/path"); break; fi
            p=$(dirname "$p")
        done
        echo "$g object_id=$oid pilote=${drv:-aucun} acpi=$acpi -> méthode ${acpi}.WM${oid}"
    done
    [ $found -eq 0 ] && echo "$g ABSENT"
done
echo "Tous les GUID WMI :"
ls /sys/bus/wmi/devices/ 2>/dev/null | tr '\n' ' '; echo

section "acpi_call"
if [ -e /proc/acpi/call ]; then echo "/proc/acpi/call présent"; else echo "absent (module acpi_call non chargé)"; fi
modinfo acpi_call 2>/dev/null | grep -E '^(filename|version|signer)' || echo "module acpi_call non installé"

section "Alimentation"
for d in /sys/class/power_supply/*; do
    echo "$(basename "$d"): type=$(cat "$d/type" 2>/dev/null) online=$(cat "$d/online" 2>/dev/null) capacity=$(cat "$d/capacity" 2>/dev/null) charge_control_end_threshold=$(cat "$d/charge_control_end_threshold" 2>/dev/null || echo -)"
done

section "GPU"
lspci -nn 2>/dev/null | grep -Ei 'vga|3d|display'
for d in /sys/bus/pci/devices/*; do
    [ "$(cat "$d/vendor" 2>/dev/null)" = "0x10de" ] && echo "$(basename "$d") class=$(cat "$d/class") runtime_status=$(cat "$d/power/runtime_status" 2>/dev/null)"
done
command -v nvidia-smi >/dev/null && echo "nvidia-smi présent" || echo "nvidia-smi absent"

section "Gestion d'énergie concurrente"
for s in power-profiles-daemon tuned tuned-ppd tlp auto-cpufreq thermald; do
    printf '%-22s %s\n' "$s" "$(systemctl is-active "$s" 2>/dev/null)"
done

section "NitroSense Linux"
systemctl is-active nitrosensed 2>/dev/null
ls -l /run/nitrosense/nitrosense.sock 2>/dev/null

section "dmesg (acer / wmi)"
dmesg 2>/dev/null | grep -iE 'acer|wmi' | tail -n 40 || echo "(dmesg nécessite sudo)"
