#!/usr/bin/env python3
"""Repair CmdVault Kitty header after the label-position patch.

* Restore the logo's fzf top margin, even if `cmdvault` was restored from .save.
* Remove the redundant top-right help hint, retaining fzf's own help line.
* Restore READY/sync text to its original row.
* Move only the Kitty sync orb 10 real pixels lower.

Never modifies cmdvault.png, the logo dimensions or the neon animation.
Uses exact anchors and refuses unknown layouts.
"""
from __future__ import annotations

import argparse
import ast
import difflib
import os
import re
from pathlib import Path
import shutil
import sys
import tempfile


OLD_PICKER = '''\tneon_picker = backend == "kitty" and sys.stdin.isatty()
\tif neon_picker:
\t\tdimensions = shutil.get_terminal_size((80, 24))
\t\tneon_picker = dimensions.columns >= 32 and dimensions.lines >= 10'''
NEW_PICKER = '''\tneon_picker = backend == "kitty" and sys.stdin.isatty()
\tdimensions = shutil.get_terminal_size((80, 24))
\tif neon_picker:
\t\tneon_picker = dimensions.columns >= 32 and dimensions.lines >= 10
\t# Leave the top six rows to the Kitty image; fzf must not draw into them.
\tlogo_header_rows = (6 if neon_picker and dimensions.columns >= 56
\t\tand dimensions.lines >= 24
\t\tand (Path(__file__).resolve().parent / "cmdvault.png").is_file() else 1)'''
OLD_MARGIN = '"--margin=1,0,0,0"'
NEW_MARGIN = 'f"--margin={logo_header_rows},0,0,0"'

OLD_READY = 'row = max(2, min(rows - 1, round(center_y / ch) + 1))'
MOVED_READY = 'row = max(2, min(rows - 2, round(center_y / ch)))'
BANNER_CALL_PATTERN = re.compile(r'(?m)^([ \t]*)emit\(top_banner_packet\(columns, rows, heading_version\)\)[ \t]*$')
BANNER_NOTE = '                            # No duplicate help hint above the logo; fzf shows it below.'

OLD_ORB = '        top_px = max(0, ph - margin - orb_px)'
NEW_ORB = '        top_px = max(0, ph - margin - orb_px + 10)'


def patch_main(src: str) -> str:
    if 'logo_header_rows =' not in src:
        if src.count(OLD_PICKER) != 1:
            raise ValueError('cmdvault: cannot locate the original Kitty picker setup')
        src = src.replace(OLD_PICKER, NEW_PICKER, 1)
    else:
        if 'logo_header_rows = (6 if neon_picker' not in src:
            raise ValueError('cmdvault: unexpected logo header calculation; refusing to override')

    if NEW_MARGIN not in src:
        if src.count(OLD_MARGIN) != 1:
            raise ValueError('cmdvault: cannot locate original --margin=1,0,0,0')
        src = src.replace(OLD_MARGIN, NEW_MARGIN, 1)
    if src.count(NEW_MARGIN) != 1:
        raise ValueError('cmdvault: expected exactly one logo-aware fzf margin')
    return src


def patch_picker(src: str) -> str:
    if 'logo.packet(columns, rows' not in src:
        raise ValueError('picker.py: KittyLogoOverlay integration was not detected')

    if MOVED_READY in src:
        if src.count(MOVED_READY) != 1 or OLD_READY in src:
            raise ValueError('picker.py: ambiguous READY positions')
        src = src.replace(MOVED_READY, OLD_READY, 1)
    elif src.count(OLD_READY) != 1:
        raise ValueError('picker.py: expected READY positioning not found')

    matches = list(BANNER_CALL_PATTERN.finditer(src))
    if matches:
        if len(matches) != 1:
            raise ValueError('picker.py: multiple help hint output calls')
        src = BANNER_CALL_PATTERN.sub(lambda match: match.group(1) +
                                      BANNER_NOTE.strip(), src, count=1)
    elif BANNER_NOTE.strip() not in src:
        raise ValueError('picker.py: duplicate help banner location not found')
    # Leave the help shortcut in fzf's normal header and the help dialog intact.
    return src


def patch_kitty(src: str) -> str:
    if 'class KittyLogoOverlay:' not in src:
        raise ValueError('kitty.py: logo renderer missing; old logo integration required')
    if NEW_ORB not in src:
        if src.count(OLD_ORB) != 1:
            raise ValueError('kitty.py: cannot locate sync orb vertical position')
        src = src.replace(OLD_ORB, NEW_ORB, 1)
    if src.count(NEW_ORB) != 1:
        raise ValueError('kitty.py: expected one adjusted sync orb position')
    return src


def backup_path(file: Path) -> Path:
    base = file.with_name(file.name + '.before-header-repair.bak')
    if not base.exists():
        return base
    idx = 2
    while True:
        option = file.with_name(file.name + f'.before-header-repair.{idx}.bak')
        if not option.exists():
            return option
        idx += 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, default=Path.cwd(), help='CmdVault project root')
    parser.add_argument('--dry-run', action='store_true', help='Validate and show a diff without writing')
    args = parser.parse_args()
    root = args.project.expanduser().resolve()
    if not (root / 'cmdvault.png').is_file():
        parser.error('cmdvault.png must exist in the CmdVault project root')
    updates: dict[Path, tuple[str, str]] = {}
    for name, patch in (
        ('cmdvault', patch_main),
        ('src/visuals/picker.py', patch_picker),
        ('src/visuals/kitty.py', patch_kitty),
    ):
        path = root / name
        before = path.read_text(encoding='utf-8')
        after = patch(before)
        ast.parse(after, filename=str(path))
        if after != before:
            updates[path] = (before, after)

    if not updates:
        print('Already repaired; nothing to change.')
        return 0
    for path, (before, after) in updates.items():
        print(f'\n--- {path.relative_to(root)} ---')
        print(''.join(difflib.unified_diff(
            before.splitlines(keepends=True), after.splitlines(keepends=True),
            fromfile='a/' + str(path.relative_to(root)),
            tofile='b/' + str(path.relative_to(root)),
        )))

    if args.dry_run:
        print('\nDry run: no changes were written.')
        return 0

    # Back up everything before touching any source file.
    backups: dict[Path, Path] = {}
    for path in updates:
        dest = backup_path(path)
        shutil.copy2(path, dest)
        backups[path] = dest

    written = []
    try:
        for path, (_, after) in updates.items():
            with tempfile.NamedTemporaryFile(
                'w', encoding='utf-8', dir=path.parent, prefix='.cmdvault-header-',
                suffix='.tmp', delete=False,
            ) as tmp:
                tmp.write(after)
                temp_path = Path(tmp.name)
            try:
                os.chmod(temp_path, path.stat().st_mode & 0o777)
                os.replace(temp_path, path)
            finally:
                temp_path.unlink(missing_ok=True)
            written.append(path)
    except BaseException:
        for path in written:
            shutil.copy2(backups[path], path)
        raise
    print('\nUpdated. Backups:')
    for path, copy in backups.items():
        print(f'  {path.relative_to(root)} -> {copy.name}')
    print('Reinstall with ./install.sh, then test in Kitty.')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, SyntaxError) as exc:
        print(f'Header repair aborted without planned changes: {exc}', file=sys.stderr)
        raise SystemExit(1)
