"""Per-game mod folders and device import for the Mods page.

Device access goes through pymobiledevice3's House Arrest/AFC client. The app already enables
file sharing, so this works on Windows with iTunes/usbmuxd as well as on macOS. No game data,
saves or other Documents folders are read.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Callable

TITLE_ID = re.compile(r'^[0-9A-Fa-f]{16}$')
DEFAULT_SHARED_BUNDLE_ID = 'org.nxnative.shell'


def title_id(value: str) -> str:
    tid = (value or '').strip().upper()
    if not TITLE_ID.fullmatch(tid):
        raise ValueError(f'not a 16-digit title ID: {value!r}')
    return tid


def game_mods_dir(root: str | os.PathLike, game: str) -> Path:
    """<root>/<title ID>, accepting a root that already is that title folder."""
    tid = title_id(game)
    base = Path(root).expanduser()
    return base if base.name.upper() == tid else base / tid


def has_mod_files(root: str | os.PathLike, game: str) -> bool:
    folder = game_mods_dir(root, game)
    try:
        return any(p.is_file() and not p.is_symlink() and not p.name.startswith('.')
                   for p in folder.rglob('*'))
    except OSError:
        return False


def kind_for_game(game, fallback: str) -> str:
    """The selected library game's actual output kind, not an unrelated current Build selection."""
    path = str(getattr(game, 'ipa_path', '') or '')
    shell_fw = str(getattr(game, 'ios_app_path', '') or '')
    if (path and path.rstrip('/\\').lower().endswith('.framework') and os.path.isdir(path)) or (
            shell_fw and shell_fw.rstrip('/\\').lower().endswith('.framework') and not path):
        return 'framework'
    if path and os.path.isfile(path):
        try:
            from .library import _ipa_kind
            real = os.path.realpath(path)
            info = os.stat(real)
            kind = _ipa_kind(real, (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns))
            if kind:
                return kind
        except OSError:
            pass
        if os.path.basename(path).lower() == 'nx2apple.ipa':
            return 'shared'
        return 'oneGame'
    if shell_fw:
        return 'shared'
    return fallback


def bundle_id_for_game(game: str, ipa_kind: str, shell_root: str | os.PathLike) -> str:
    """The installed app whose Documents/mods/<game> the Import button reads."""
    tid = title_id(game)
    if ipa_kind == 'oneGame':
        return f'org.nxnative.{tid.lower()}'
    try:
        data = json.loads((Path(shell_root) / 'shell.json').read_text(encoding='utf-8'))
        bundle = str(data.get('bundleID') or '').strip()
    except (OSError, ValueError, UnicodeDecodeError):
        bundle = ''
    return bundle or DEFAULT_SHARED_BUNDLE_ID


DEVICE_PACKAGE = 'pymobiledevice3==11.63.1'


def ensure_device_support(python: str, runs: Callable[[str, list[str]], bool]) -> bool:
    """Install the device client into the app's existing build venv, never the system Python."""
    if runs(python, ['-c', "import importlib.metadata; assert importlib.metadata.version('pymobiledevice3') == '11.63.1'"]):
        return True
    return runs(python, ['-m', 'pip', 'install', DEVICE_PACKAGE])


def list_device_ids(python: str, runner: Callable[..., subprocess.CompletedProcess], timeout: int = 30) -> list[str]:
    """USB devices in usbmuxd's order, without opening lockdown or prompting the user."""
    try:
        result = runner([python, '-m', 'pymobiledevice3', 'usbmux', 'list', '--simple'],
                        capture_output=True, text=True, timeout=timeout)
        payload = json.loads(result.stdout or '[]') if result.returncode == 0 else []
    except (OSError, subprocess.SubprocessError, ValueError):
        return []
    return [str(x) for x in payload if isinstance(x, str) and x.strip()] if isinstance(payload, list) else []


def installed_bundle_id(
    python: str,
    preferred: str,
    game: str,
    shared: bool,
    runner: Callable[..., subprocess.CompletedProcess],
    timeout: int = 60,
    device_id: str = '',
) -> str:
    """Prefer the recorded ID, but accept a sideloader's team suffix on the installed app."""
    tid = title_id(game).lower()
    try:
        result = runner([python, '-m', 'pymobiledevice3', 'apps', 'list'],
                        capture_output=True, text=True, timeout=timeout,
                        env={**os.environ, 'PYMOBILEDEVICE3_UDID': device_id})
        payload = json.loads(result.stdout or '{}') if result.returncode == 0 else {}
    except (OSError, subprocess.SubprocessError, ValueError):
        return preferred
    if isinstance(payload, dict):
        candidates = [str(k) for k in payload]
    elif isinstance(payload, list):
        candidates = [str(v.get('bundleIdentifier') or v.get('bundleID') or '')
                      for v in payload if isinstance(v, dict)]
    else:
        candidates = []
    base = DEFAULT_SHARED_BUNDLE_ID if shared else f'org.nxnative.{tid}'
    for wanted in (preferred, base):
        if wanted in candidates:
            return wanted
    for wanted in (preferred, base):
        matches = sorted(c for c in candidates if c.startswith(wanted + '.'))
        if matches:
            return matches[0]
    return preferred


def import_from_device(
    *,
    python: str,
    bundle_id: str,
    title: str,
    mods_root: str | os.PathLike,
    shared: bool = False,
    device_id: str = '',
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    timeout: int = 180,
) -> tuple[bool, str]:
    """Merge the app's per-game mods into the PC folder. Existing unrelated mods stay.

    One-game apps use Documents/mods/<title>. The shared shell uses
    Documents/games/<title>/mods. A staged merge is swapped in only after every local copy
    succeeds, so a failed transfer or local disk error cannot leave a partial PC tree.
    """
    tid = title_id(title)
    destination = game_mods_dir(mods_root, tid)
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.mods-import-', dir=destination.parent))
    try:
        devices = [device_id] if device_id else list_device_ids(python, runner)
        if not devices:
            return False, 'no connected iPhone or iPad'
        udid = device_id or devices[0]
        bundle_id = installed_bundle_id(python, bundle_id, tid, shared, runner, device_id=udid)
        remote = f'/Documents/games/{tid}/mods' if shared else f'/Documents/mods/{tid}'
        command = [python, '-m', 'pymobiledevice3', 'apps', 'pull', bundle_id, remote, str(staging)]
        try:
            result = runner(command, capture_output=True, text=True, timeout=timeout,
                            env={**os.environ, 'PYMOBILEDEVICE3_UDID': udid})
        except (OSError, subprocess.SubprocessError) as exc:
            return False, str(exc)
        if result.returncode != 0:
            detail = (result.stdout or result.stderr or f'exit {result.returncode}').strip()
            return False, detail.splitlines()[-1] if detail else f'exit {result.returncode}'
        imported = staging / ('mods' if shared else tid)
        if not imported.is_dir():
            return False, f'no mods folder in {bundle_id}:{remote}'
        if any(p.is_symlink() for p in imported.rglob('*')):
            return False, 'the imported mods folder contains a symbolic link'
        merged = staging / 'merged'
        if destination.exists():
            shutil.copytree(destination, merged)
        shutil.copytree(imported, merged, dirs_exist_ok=True)
        previous = staging / 'previous'
        try:
            if destination.exists():
                os.replace(destination, previous)
            os.replace(merged, destination)
        except OSError:
            if previous.is_dir() and not destination.exists():
                os.replace(previous, destination)
            raise
        return True, str(destination)
    except OSError as exc:
        return False, str(exc)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
