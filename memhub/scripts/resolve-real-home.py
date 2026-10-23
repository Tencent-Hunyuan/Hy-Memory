"""Resolve the real user home without trusting environment overrides.

On POSIX the passwd database is authoritative, so a spoofed HOME cannot widen
the sandbox. Windows has no env-independent equivalent that is portable, so ask
the profile API for the calling token's own profile directory and refuse to
guess rather than read back a variable the isolation guard is meant to police.
"""

import os
import sys


def from_passwd():
    import pwd

    return pwd.getpwuid(os.getuid()).pw_dir


def from_token():
    import ctypes
    from ctypes import wintypes

    token = wintypes.HANDLE()
    userenv32 = ctypes.WinDLL("userenv", use_last_error=True)
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    # Without these, the -1 pseudo-handle is truncated to 32 bits and
    # OpenProcessToken rejects it on a 64-bit process.
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    advapi32.OpenProcessToken.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    if not advapi32.OpenProcessToken(
        kernel32.GetCurrentProcess(), 0x0008, ctypes.byref(token)
    ):
        raise OSError(f"OpenProcessToken failed: {ctypes.get_last_error()}")
    size = wintypes.DWORD()
    userenv32.GetUserProfileDirectoryW(token, None, ctypes.byref(size))
    buffer = ctypes.create_unicode_buffer(size.value)
    if not userenv32.GetUserProfileDirectoryW(token, buffer, ctypes.byref(size)):
        raise OSError(
            f"GetUserProfileDirectoryW failed: {ctypes.get_last_error()}"
        )
    return buffer.value


def to_native(posix_path):
    """Convert an MSYS/Cygwin path to the form Node resolves on Windows."""
    import subprocess

    return subprocess.run(
        ["cygpath", "-w", posix_path],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def resolve():
    # Keyed on os.name, not hasattr(os, "getuid"): an MSYS/Cygwin build of
    # CPython has getuid and would otherwise return `/c/Users/...`, which Node
    # resolves against the current drive, silently defeating the guard.
    if os.name == "nt":
        return from_token()
    if sys.platform in ("msys", "cygwin"):
        return to_native(from_passwd())
    if hasattr(os, "getuid"):
        return from_passwd()
    return os.path.expanduser("~")


def main():
    try:
        sys.stdout.write(f"{resolve()}\n")
    except Exception as error:
        sys.stderr.write(
            f"cannot resolve the real home ({error}); "
            "set ONE_MEMORY_TEST_REAL_HOME explicitly\n"
        )
        return 78
    return 0


if __name__ == "__main__":
    sys.exit(main())
