"""Ask macOS (CoreAudio) about an output device's sample rate.

sounddevice can't tell us what rate a device is *actually* running at, so we
ask macOS directly. We use ctypes, a built-in Python module for calling
functions written in C (the language macOS's audio system is written in).

The C pattern used everywhere below: make an empty "box" (buffer) of the right
type, pass it to macOS, and macOS fills it in. A non-zero return value means
macOS reported an error.
"""

import ctypes
from ctypes import POINTER, Structure, byref, c_double, c_uint32, c_void_p, sizeof

_coreaudio = ctypes.CDLL("/System/Library/Frameworks/CoreAudio.framework/CoreAudio")
_corefoundation = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")


def _code(text: str) -> int:
    """CoreAudio names its properties with 4-letter codes, stored as one number."""
    return int.from_bytes(text.encode("ascii"), "big")


# The 4-letter codes we need (from Apple's CoreAudio documentation)
_SYSTEM = 1                              # the "whole audio system" object
_DEVICES = _code("dev#")                 # list of all audio devices
_NAME = _code("lnam")                    # a device's name
_RATE = _code("nsrt")                    # a device's current sample rate
_AVAILABLE_RATES = _code("nsr#")         # rates a device supports
_SCOPE_GLOBAL = _code("glob")
_ELEMENT_MAIN = 0
_UTF8 = 0x08000100                       # text encoding code for UTF-8


class _Address(Structure):
    """Which property to read: (what, scope, element), as CoreAudio expects."""
    _fields_ = [("selector", c_uint32), ("scope", c_uint32), ("element", c_uint32)]


class _RateRange(Structure):
    """A supported rate range. For most devices min == max (one exact rate)."""
    _fields_ = [("minimum", c_double), ("maximum", c_double)]


# Tell ctypes the argument types of the C functions we call
_coreaudio.AudioObjectGetPropertyDataSize.argtypes = [
    c_uint32, POINTER(_Address), c_uint32, c_void_p, POINTER(c_uint32)]
_coreaudio.AudioObjectGetPropertyData.argtypes = [
    c_uint32, POINTER(_Address), c_uint32, c_void_p, POINTER(c_uint32), c_void_p]
_corefoundation.CFStringGetCString.argtypes = [c_void_p, ctypes.c_char_p, ctypes.c_long, c_uint32]
_corefoundation.CFRelease.argtypes = [c_void_p]


def _size_of(obj: int, selector: int) -> int:
    """How many bytes the property's value takes."""
    address = _Address(selector, _SCOPE_GLOBAL, _ELEMENT_MAIN)
    size = c_uint32(0)
    _coreaudio.AudioObjectGetPropertyDataSize(obj, byref(address), 0, None, byref(size))
    return size.value


def _read(obj: int, selector: int, box) -> bool:
    """Fill `box` with the property's value. Returns True if it worked."""
    address = _Address(selector, _SCOPE_GLOBAL, _ELEMENT_MAIN)
    size = c_uint32(sizeof(box))
    status = _coreaudio.AudioObjectGetPropertyData(obj, byref(address), 0, None, byref(size), byref(box))
    return status == 0


def _device_name(device_id: int) -> str:
    text_ref = c_void_p()
    if not _read(device_id, _NAME, text_ref) or not text_ref.value:
        return ""
    buffer = ctypes.create_string_buffer(512)
    _corefoundation.CFStringGetCString(text_ref, buffer, len(buffer), _UTF8)
    _corefoundation.CFRelease(text_ref)  # macOS gave us this text; we must free it
    return buffer.value.decode("utf-8")


def _find_device(name: str) -> int | None:
    """Find a device's CoreAudio ID by its name (the same name sounddevice shows)."""
    count = _size_of(_SYSTEM, _DEVICES) // sizeof(c_uint32)
    ids = (c_uint32 * count)()
    if not _read(_SYSTEM, _DEVICES, ids):
        return None
    for device_id in ids:
        if _device_name(device_id) == name:
            return device_id
    return None


def current_rate(device_name: str) -> float | None:
    """The sample rate the device is running at right now, or None if unknown."""
    device_id = _find_device(device_name)
    rate = c_double()
    if device_id is None or not _read(device_id, _RATE, rate):
        return None
    return rate.value


def supported_rates(device_name: str) -> list[float]:
    """The sample rates the device can run at natively."""
    device_id = _find_device(device_name)
    if device_id is None:
        return []
    count = _size_of(device_id, _AVAILABLE_RATES) // sizeof(_RateRange)
    ranges = (_RateRange * count)()
    if not _read(device_id, _AVAILABLE_RATES, ranges):
        return []
    return sorted({r.minimum for r in ranges} | {r.maximum for r in ranges})
