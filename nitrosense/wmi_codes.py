"""Acer gaming WMI encodings, as used by NitroSense 3.1 on the Nitro AN515-58.

Where these values come from (read on the Windows side of this laptop):
- WMI classes in root\\WMI (BIOS V2.21, Acer WMI v2.86): AcerGamingFunction,
  APGeAction and BatteryControl, with their method ids and parameter types.
- The NitroSense 3.1.3052 app (.NET), disassembled: CommonFunction,
  Popup_Lighting, LightingDynamicUI. See docs/REVERSE_ENGINEERING.md.

Everything here is pure: functions only build or decode integers and bytes.
"Get" methods take a 4-byte (u32) input, "Set" methods an 8-byte (u64) input,
except SetGamingKBBacklight (16 bytes) and the BatteryControl methods.
The low byte of every integer result is a status code: 0 means success.
"""
from __future__ import annotations

GAMING_GUID = "7A4DDFE7-5B5D-40B4-8595-4408E0CC7F56"   # AcerGamingFunction
APGE_GUID = "61EF69EA-865C-4BC3-A502-A0DEBA0CB531"     # APGeAction
BATTERY_GUID = "79772EC5-04B1-4BFD-843C-61E7F77B6CC9"  # BatteryControl

GUID_ALIASES = {"gaming": GAMING_GUID, "apge": APGE_GUID, "battery": BATTERY_GUID}

# --- AcerGamingFunction method ids (WmiMethodId in the MOF) ---
SET_GAMING_PROFILE = 1
GET_GAMING_PROFILE = 3
GET_GAMING_SYS_INFO = 5
SET_GAMING_RGB_KB = 6
GET_GAMING_RGB_KB = 7
SET_GAMING_LED_BEHAVIOR = 10
SET_GAMING_FAN_BEHAVIOR = 14
GET_GAMING_FAN_BEHAVIOR = 15
SET_GAMING_FAN_SPEED = 16
GET_GAMING_FAN_SPEED = 17
SET_GAMING_KB_BACKLIGHT = 20
GET_GAMING_KB_BACKLIGHT = 21
SET_GAMING_MISC_SETTING = 22
GET_GAMING_MISC_SETTING = 23

# --- APGeAction method ids ---
APGE_SET_FUNCTION = 1
APGE_GET_FUNCTION = 2

# --- BatteryControl method ids ---
BATTERY_GET_HEALTH_STATUS = 20
BATTERY_SET_HEALTH_CONTROL = 21


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, int(value)))


def status_ok(raw: int) -> bool:
    """True when the status byte (bits 0-7) of a WMI integer result is 0."""
    return (int(raw) & 0xFF) == 0


# ---------------------------------------------------------------------------
# Fans (SetGamingFanBehavior / SetGamingFanSpeed)
#   behavior: bits 0-15 = fan groups (CPU 0x0001, GPU 0x0008),
#             CPU mode in bits 16-17, GPU mode in bits 22-23
#   speed:    bits 0-7 = fan id (CPU 0x01, GPU 0x04), bits 8-15 = percent
# NitroSense: Auto = 0x00410009, Max = 0x00820009, Custom = 0x00C30009.
# ---------------------------------------------------------------------------
FAN_MODE_AUTO = 1
FAN_MODE_MAX = 2
FAN_MODE_CUSTOM = 3
FAN_MODES = {"auto": FAN_MODE_AUTO, "max": FAN_MODE_MAX, "custom": FAN_MODE_CUSTOM}
FANS = ("cpu", "gpu")
FAN_GROUP_BIT = {"cpu": 0x0001, "gpu": 0x0008}
FAN_MODE_SHIFT = {"cpu": 16, "gpu": 22}
FAN_SPEED_ID = {"cpu": 0x01, "gpu": 0x04}


def fan_behavior(cpu_mode: int | None, gpu_mode: int | None) -> int:
    """Input for SetGamingFanBehavior. A mode of None leaves that fan alone."""
    value = 0
    for fan, mode in (("cpu", cpu_mode), ("gpu", gpu_mode)):
        if mode is None:
            continue
        if mode not in (FAN_MODE_AUTO, FAN_MODE_MAX, FAN_MODE_CUSTOM):
            raise ValueError(f"invalid fan mode {mode!r}")
        value |= FAN_GROUP_BIT[fan] | (mode << FAN_MODE_SHIFT[fan])
    return value


def fan_behavior_all(mode: int) -> int:
    """NitroSense set_all_fan_mode(): same mode on both fans."""
    return fan_behavior(mode, mode)


def fan_speed(fan: str, percent: int) -> int:
    """Input for SetGamingFanSpeed (NitroSense set_single_custom_fan_speed)."""
    return FAN_SPEED_ID[fan] | (_clamp(percent, 0, 100) << 8)


def fan_speed_query(fan: str) -> int:
    """Input for GetGamingFanSpeed. Not used by NitroSense: diagnostics only."""
    return FAN_SPEED_ID[fan]


def parse_fan_behavior(raw: int) -> dict | None:
    """Decode GetGamingFanBehavior(0x0009). UNVERIFIED layout (assumed to mirror
    the setter shifted down by 8 bits); used for diagnostics only."""
    if not status_ok(raw):
        return None
    return {"cpu": (raw >> 8) & 0x3, "gpu": (raw >> 14) & 0x3}


# ---------------------------------------------------------------------------
# Sensors (GetGamingSysInfo): input = 0x01 | sensor << 8,
# output = status (bits 0-7) | value << 8 (16 bits: degrees C or RPM).
# Sensor ids = NitroSense System_Health_Information_Index.
# ---------------------------------------------------------------------------
SENSOR_CPU_TEMP = 0x01
SENSOR_CPU_FAN = 0x02
SENSOR_SYSTEM_TEMP = 0x03
SENSOR_SYSTEM_FAN = 0x04
SENSOR_GPU_FAN = 0x06
SENSOR_SYSTEM2_TEMP = 0x07
SENSOR_GPU_TEMP = 0x0A
SYSINFO_BATTERY_BOOST_QUERY = 0x0002


def sysinfo_query(sensor: int) -> int:
    return 0x0001 | ((sensor & 0xFF) << 8)


def parse_sysinfo(raw: int) -> int | None:
    if not status_ok(raw):
        return None
    return (int(raw) >> 8) & 0xFFFF


# ---------------------------------------------------------------------------
# Misc settings (SetGamingMiscSetting / GetGamingMiscSetting)
#   set input = index | value << 8 ; get input = index ;
#   get output = status | value << 8
# Operating modes = NitroSense OPMode_Type (Quiet 0, Default 1, Extreme 4,
# Turbo 5). The Linux acer-wmi driver uses the same values (0x0B index).
# NitroSense on this laptop shows Quiet / Default / Performance(=4).
# ---------------------------------------------------------------------------
MISC_GPU_MODE = 0x02
MISC_GPU_MODE_SUPPORT = 0x09
MISC_SUPPORTED_PROFILES = 0x0A
MISC_PLATFORM_PROFILE = 0x0B

OPMODE = {"quiet": 0x00, "default": 0x01, "performance": 0x04, "turbo": 0x05, "eco": 0x06}
OPMODE_NAMES = {value: name for name, value in OPMODE.items()}

# NitroSense CommonFunction.Set_System_GPUMode(True) writes 2 ("discrete GPU only").
GPU_MUX_NAMES = {1: "hybrid", 2: "discrete"}


def misc_set(index: int, value: int) -> int:
    return (index & 0xFF) | ((value & 0xFF) << 8)


def parse_misc(raw: int) -> int | None:
    if not status_ok(raw):
        return None
    return (int(raw) >> 8) & 0xFF


def parse_supported_profiles(raw: int) -> list[str] | None:
    """Bitmap of supported operating modes (bit n = mode value n)."""
    bitmap = parse_misc(raw)
    if bitmap is None:
        return None
    return [name for value, name in sorted(OPMODE_NAMES.items()) if bitmap & (1 << value)]


# ---------------------------------------------------------------------------
# Keyboard RGB (4 zones)
#   SetGamingKBBacklight (16 bytes):
#     [0] effect  [1] speed  [2] brightness 0-100  [3] 0x08 for wave only
#     [4] direction (wave/shifting)  [5..7] R, G, B  [8..15] zero
#   SetGamingRgbKb (u64): zone bitmap (bits 0-3) | R << 8 | G << 16 | B << 24
#   SetGamingLEDBehavior (u64): 0x08 | zone n enabled -> bit (40 + n)
# NitroSense brightness slider has 5 steps: (level - 1) * 25 -> 0..100.
# ---------------------------------------------------------------------------
KB_EFFECTS = {"static": 0, "breathing": 1, "neon": 2, "wave": 3, "shifting": 4, "zoom": 5}
KB_DIRECTIONS = {"right": 1, "left": 2}
KB_ZONES = 4
KB_EFFECTS_WITH_COLOR = ("breathing", "wave", "shifting", "zoom")
KB_EFFECTS_WITH_DIRECTION = ("wave", "shifting")


def parse_color(color) -> tuple[int, int, int]:
    """Accept '#RRGGBB', 'RRGGBB' or an (r, g, b) tuple."""
    if isinstance(color, (tuple, list)) and len(color) == 3:
        return tuple(_clamp(c, 0, 255) for c in color)  # type: ignore[return-value]
    text = str(color).strip().lstrip("#")
    if len(text) != 6:
        raise ValueError(f"invalid color {color!r}")
    return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)


def kb_backlight_payload(effect: str, brightness: int = 100, speed: int = 0,
                         direction: str = "right", color=(0, 0, 0)) -> bytes:
    if effect not in KB_EFFECTS:
        raise ValueError(f"unknown keyboard effect {effect!r}")
    payload = bytearray(16)
    payload[0] = KB_EFFECTS[effect]
    payload[1] = 0 if effect == "static" else _clamp(speed, 0, 9)
    payload[2] = _clamp(brightness, 0, 100)
    if effect == "wave":
        payload[3] = 0x08
    if effect in KB_EFFECTS_WITH_DIRECTION:
        if direction not in KB_DIRECTIONS:
            raise ValueError(f"unknown direction {direction!r}")
        payload[4] = KB_DIRECTIONS[direction]
    if effect in KB_EFFECTS_WITH_COLOR:
        payload[5], payload[6], payload[7] = parse_color(color)
    return bytes(payload)


def rgb_zone(zone_bitmap: int, color) -> int:
    r, g, b = parse_color(color)
    return (zone_bitmap & 0x0F) | (r << 8) | (g << 16) | (b << 24)


def zone_bit(index: int) -> int:
    if not 0 <= index < KB_ZONES:
        raise ValueError(f"zone index out of range: {index}")
    return 1 << index


def zones_enable(states) -> int:
    value = 0x08
    for index, enabled in enumerate(states):
        if enabled:
            value |= 1 << (40 + index)
    return value


# ---------------------------------------------------------------------------
# Keyboard backlight timeout ("Backlight off after 30 seconds"), APGeAction:
#   get input = 0x01 | hotkey << 8 | 0x0008 << 16           (u32)
#   set input = 0x02 | hotkey << 8 | 0x0008 << 16
#               | brightness << 32 | (30 if enabled else 0) << 40
#   get output: status (bits 0-7), brightness (32-39), timeout (40-47)
# hotkey = BK_Hotkey_Number from the NitroSense registry key (132 here).
# ---------------------------------------------------------------------------
KB_HOTKEY_NUMBER = 0x84
APGE_DEVICE_KB_BACKLIGHT = 0x0008
KB_TIMEOUT_SECONDS = 0x1E


def apge_kb_backlight_query(hotkey: int = KB_HOTKEY_NUMBER) -> int:
    return 0x01 | ((hotkey & 0xFF) << 8) | (APGE_DEVICE_KB_BACKLIGHT << 16)


def apge_kb_backlight_set(brightness: int, timeout_enabled: bool,
                          hotkey: int = KB_HOTKEY_NUMBER) -> int:
    timeout = KB_TIMEOUT_SECONDS if timeout_enabled else 0
    return (0x02 | ((hotkey & 0xFF) << 8) | (APGE_DEVICE_KB_BACKLIGHT << 16)
            | (_clamp(brightness, 0, 100) << 32) | (timeout << 40))


def parse_apge_kb_backlight(raw: int) -> dict | None:
    if not status_ok(raw):
        return None
    raw = int(raw)
    timeout = (raw >> 40) & 0xFF
    return {"brightness": (raw >> 32) & 0xFF, "timeout": timeout,
            "timeout_enabled": timeout == KB_TIMEOUT_SECONDS}


# ---------------------------------------------------------------------------
# Battery health ("charge limit 80%", Acer Care Center), BatteryControl:
#   get input  = {u8 battery=1, u8 query=1, u8[2] 0}
#   get output = {u8 function_list, u8[2] return, u8[5] function_status}
#   set input  = {u8 battery=1, u8 mask, u8 status, u8[5] 0}
#   set output = {u16 return, u16 reserved}
# mask 0x01 = health mode (limit 80%), 0x02 = calibration.
# Same layout as the out-of-tree acer-wmi-battery driver; check on Linux.
# ---------------------------------------------------------------------------
BATTERY_HEALTH_MODE = 0x01
BATTERY_CALIBRATION_MODE = 0x02


def battery_health_query(battery: int = 1, query: int = 0x01) -> bytes:
    return bytes([battery & 0xFF, query & 0xFF, 0, 0])


def battery_health_set(enabled: bool, battery: int = 1, mask: int = BATTERY_HEALTH_MODE) -> bytes:
    return bytes([battery & 0xFF, mask & 0xFF, 1 if enabled else 0, 0, 0, 0, 0, 0])


def parse_battery_health(out: bytes) -> dict | None:
    if len(out) < 8:
        return None
    return {
        "function_list": out[0],
        "return": out[1] | (out[2] << 8),
        "health_mode": bool(out[3]),
        "calibration_mode": bool(out[4]),
    }


def parse_battery_set(out: bytes) -> int | None:
    if len(out) < 2:
        return None
    return out[0] | (out[1] << 8)
