"""Pre-launch validation checks for MUSE.

Run standalone:  python -m muse.preflight
Or import:       from muse.preflight import run_checks
"""

from __future__ import annotations

import importlib
import os
import shutil
import socket
import subprocess
import sys

# ANSI colors (graceful degrade on Windows without VT support)
_GREEN = "\033[92m"
_YELLOW = "\033[93m"
_RED = "\033[91m"
_RESET = "\033[0m"

_passed = 0
_warned = 0
_failed = 0


def _ok(msg: str) -> None:
    global _passed
    _passed += 1
    print(f"  {_GREEN}[OK]{_RESET}  {msg}")


def _warn(msg: str) -> None:
    global _warned
    _warned += 1
    print(f"  {_YELLOW}[!!]{_RESET}  {msg}")


def _fail(msg: str) -> None:
    global _failed
    _failed += 1
    print(f"  {_RED}[FAIL]{_RESET} {msg}")


# ------------------------------------------------------------------
# Checks
# ------------------------------------------------------------------


def check_python_version(min_major: int = 3, min_minor: int = 10) -> None:
    v = sys.version_info
    if v.major >= min_major and v.minor >= min_minor:
        _ok(f"Python {v.major}.{v.minor}.{v.micro}")
    else:
        _fail(f"Python {v.major}.{v.minor} - need {min_major}.{min_minor}+")


def check_node_version(min_major: int = 18) -> None:
    node_cmd = shutil.which("node")
    if node_cmd is None:
        _warn("Node.js not found - frontend will not be available")
        _warn("Install Node.js 18+ from https://nodejs.org/")
        return

    try:
        result = subprocess.run(
            [node_cmd, "--version"],
            capture_output=True, text=True, timeout=5,
        )
        ver = result.stdout.strip().lstrip("v")
        major = int(ver.split(".")[0])
        if major >= min_major:
            _ok(f"Node.js {ver}")
        else:
            _fail(f"Node.js {ver} - need {min_major}+. Download from https://nodejs.org/")
    except Exception as e:
        _warn(f"Could not check Node.js version: {e}")


def check_port(port: int, name: str) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        result = s.connect_ex(("127.0.0.1", port))
    if result != 0:
        _ok(f"Port {port} ({name}) is free")
    else:
        _fail(f"Port {port} ({name}) is already in use - kill the process or change the port")


_CORE_MODULES = [
    ("fastapi", "fastapi"),
    ("uvicorn", "uvicorn[standard]"),
    ("aiosqlite", "aiosqlite"),
    ("httpx", "httpx"),
    ("pydantic", "pydantic"),
    ("websockets", "websockets"),
    ("aiohttp", "aiohttp"),
    ("cryptography", "cryptography"),
    ("tiktoken", "tiktoken"),
    ("keyring", "keyring"),
    ("psutil", "psutil"),
]

_NATIVE_MODULES = [
    ("sqlite_vec", "sqlite-vec"),
    ("sentence_transformers", "sentence-transformers"),
]


def check_imports() -> None:
    for mod_name, pkg_name in _CORE_MODULES:
        try:
            importlib.import_module(mod_name)
            _ok(f"{pkg_name}")
        except ImportError as e:
            _fail(f"{pkg_name} - {e}")

    for mod_name, pkg_name in _NATIVE_MODULES:
        try:
            importlib.import_module(mod_name)
            _ok(f"{pkg_name} (native)")
        except ImportError as e:
            _fail(f"{pkg_name} (native) - {e}")
        except Exception as e:
            _warn(f"{pkg_name} (native) - loads but errored: {e}")


def check_muse_imports() -> None:
    """Verify MUSE's own top-level modules can be imported."""
    for mod in ["muse.config", "muse.api.app", "muse.kernel.orchestrator"]:
        try:
            importlib.import_module(mod)
            _ok(f"{mod}")
        except Exception as e:
            _fail(f"{mod} - {e}")


def check_data_dir() -> None:
    from muse.config import _default_data_dir

    data_dir = _default_data_dir()
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        probe = data_dir / ".write_probe"
        probe.write_text("ok")
        probe.unlink()
        _ok(f"Data dir writable: {data_dir}")
    except OSError as e:
        _fail(f"Data dir not writable ({data_dir}): {e}")


def check_sdk() -> None:
    try:
        import muse_sdk  # noqa: F401
        _ok("muse_sdk package")
    except ImportError:
        _fail("muse_sdk not installed - run: pip install -e sdk")


# ------------------------------------------------------------------
# Runner
# ------------------------------------------------------------------


def run_checks(*, backend_port: int = 8080, frontend_port: int = 3000) -> bool:
    """Run all preflight checks. Returns True if no failures."""
    global _passed, _warned, _failed
    _passed = _warned = _failed = 0

    print("\n  Preflight checks")
    print("  " + "=" * 40)

    print("\n  Python")
    check_python_version()

    print("\n  Node.js")
    check_node_version()

    print("\n  Ports")
    check_port(backend_port, "backend")
    check_port(frontend_port, "frontend")

    print("\n  Core dependencies")
    check_imports()

    print("\n  MUSE modules")
    check_muse_imports()
    check_sdk()

    print("\n  Environment")
    check_data_dir()

    print("\n  " + "=" * 40)
    parts = [f"{_GREEN}{_passed} passed{_RESET}"]
    if _warned:
        parts.append(f"{_YELLOW}{_warned} warnings{_RESET}")
    if _failed:
        parts.append(f"{_RED}{_failed} failed{_RESET}")
    print(f"  {', '.join(parts)}")

    if _failed:
        print(f"\n  {_RED}Fix the failures above before starting MUSE.{_RESET}\n")
    else:
        print(f"\n  {_GREEN}Ready to launch.{_RESET}\n")

    return _failed == 0


if __name__ == "__main__":
    # Enable VT100 escape sequences on Windows 10+
    if os.name == "nt":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
        except Exception:
            pass

    ok = run_checks()
    sys.exit(0 if ok else 1)
