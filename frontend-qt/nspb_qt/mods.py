"""Per-game mod folders for the Mods page.

The player picks a folder on this PC. It can be the mod itself (an exefs/ or romfs/ folder),
a folder holding one subfolder per mod, or a mods root with a <title ID> folder below it: the
engine's mod_apply.resolve_root() reads all three, and the checks here mirror it.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

TITLE_ID = re.compile(r'^[0-9A-Fa-f]{16}$')


def title_id(value: str) -> str:
    tid = (value or '').strip().upper()
    if not TITLE_ID.fullmatch(tid):
        raise ValueError(f'not a 16-digit title ID: {value!r}')
    return tid


def game_mods_dir(root: str | os.PathLike, game: str) -> Path:
    """The folder the engine reads for this game, as mod_apply.resolve_root() resolves it.

    <root>/<title ID> (or an Atmosphère <root>/contents/<title ID>) when that folder exists,
    else the picked folder itself, so choosing a mod directly needs no title-ID subfolder.
    """
    tid = title_id(game)
    base = Path(root).expanduser()
    for candidate in (base / tid, base / 'contents' / tid):
        if candidate.is_dir():
            return candidate
    return base


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
