#!/usr/bin/env python3
"""Add a transparent CmdVault header logo to the existing Kitty neon picker.

Edits the repository in place, refusing unexpected source layouts.
Run from the project root, or supply --project. Re-run is idempotent.
"""
from __future__ import annotations

import argparse
import ast
from pathlib import Path
import sys


LOGO_CLASS = '''

class KittyLogoOverlay:
    """Static, alpha-preserving logo in fzf's reserved top margin."""

    def __init__(self, path, image_id):
        from pathlib import Path
        self.path = Path(path)
        self.image_id = image_id
        self.key = None
        self.placed_at = None

    def invalidate(self):
        # Terminal erasures or geometry changes may discard the image.
        self.key = None
        self.placed_at = None

    def clear(self):
        self.invalidate()
        return command(f'a=d,d=I,i={self.image_id},q=2')

    def packet(self, columns, rows, *, pixel_width=None, pixel_height=None):
        if not self.path.is_file() or columns < 56 or rows < 24:
            return ''
        from PIL import Image

        pw, ph = pixel_width or columns * 8, pixel_height or rows * 16
        cw, ch = pw / columns, ph / rows
        # Six top rows are reserved by cmdvault's fzf margin. Keep the
        # outermost cell free for the existing animated neon tube.
        max_height = max(1, int(min(94, 4.5 * ch)))
        max_width = max(1, int(min(500, pw - 8 * cw)))
        with Image.open(self.path) as source:
            logo = source.convert('RGBA')
            logo.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)
        if logo.width <= 0 or logo.height <= 0:
            return ''

        key = (pw, ph, columns, rows, logo.size)
        result = ''
        if key != self.key:
            result += self.clear()
            result += encode_rgba(logo.tobytes(), logo.width, logo.height,
                                  self.image_id, compressed=True)
            self.key = key

        # Center within the reserved header, below the top neon tube.
        left = max(0, (pw - logo.width) // 2)
        top = max(0, round(ch + (4.4 * ch - logo.height) / 2))
        col = max(1, int(left // cw) + 1)
        row = max(1, int(top // ch) + 1)
        ox = max(0, int(left - (col - 1) * cw))
        oy = max(0, int(top - (row - 1) * ch))
        placement = (col, row, ox, oy)
        if placement != self.placed_at:
            result += ('\\x1b7' + f'\\x1b[{row};{col}H' + command(
                f'a=p,i={self.image_id},p=1,X={ox},Y={oy},z=3,C=1,q=2') + '\\x1b8')
            self.placed_at = placement
        return result
''' 


def replace_once(text: str, old: str, new: str, name: str):
    if new in text:
        return text
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f'{name}: expected one insertion point, found {count}')
    return text.replace(old, new, 1)


def patch_cmdvault(source: str) -> str:
    old = '''\tneon_picker = backend == "kitty" and sys.stdin.isatty()
\tif neon_picker:
\t\tdimensions = shutil.get_terminal_size((80, 24))
\t\tneon_picker = dimensions.columns >= 32 and dimensions.lines >= 10'''
    new = '''\tneon_picker = backend == "kitty" and sys.stdin.isatty()
\tdimensions = shutil.get_terminal_size((80, 24))
\tif neon_picker:
\t\tneon_picker = dimensions.columns >= 32 and dimensions.lines >= 10
\t# The Kitty logo belongs inside fzf's top margin, not on top of entries.
\tlogo_header_rows = (6 if neon_picker and dimensions.columns >= 56
\t\tand dimensions.lines >= 24
\t\tand (Path(__file__).resolve().parent / "cmdvault.png").is_file() else 1)'''
    source = replace_once(source, old, new, 'cmdvault')
    old2 = '''argv += ["--no-clear", "--height=100%", ("--margin=1,2,0,2" if backend == "sixel" else "--margin=1,0,0,0")]'''
    new2 = '''argv += ["--no-clear", "--height=100%", ("--margin=1,2,0,2" if backend == "sixel" else f"--margin={logo_header_rows},0,0,0")]'''
    return replace_once(source,old2,new2,'cmdvault fzf margin')


def patch_kitty(source: str) -> str:
    anchor = 'class KittyDecoration:'
    if 'class KittyLogoOverlay:' in source:
        return source
    return replace_once(source,anchor,LOGO_CLASS+'\n\n'+anchor,'kitty.py')


def patch_picker(source: str) -> str:
    source = replace_once(source,
        'from .kitty import KittyDecoration, AnimationLoader, KittyPulseOverlay',
        'from .kitty import KittyDecoration, AnimationLoader, KittyPulseOverlay, KittyLogoOverlay',
        'picker.py import')
    old = '''    pulse = KittyPulseOverlay(config, pulse_id)
    # Explicit, local-only visual preview; no sync API calls or history access.'''
    new = '''    pulse = KittyPulseOverlay(config, pulse_id)
    logo_id = secrets.randbelow(2**31 - 1) + 1
    while logo_id in (pulse_id, decoration.image_id):
        logo_id = secrets.randbelow(2**31 - 1) + 1
    logo = KittyLogoOverlay(Path(__file__).resolve().parents[2] / "cmdvault.png", logo_id)
    # Explicit, local-only visual preview; no sync API calls or history access.'''
    source = replace_once(source,old,new,'picker.py logo constructor')
    source = replace_once(source,
        '''                    ready_to_place = True
                    pulse_needs_draw = True
                    heading_dirty = True
                    error_drawn = False''',
        '''                    ready_to_place = True
                    pulse_needs_draw = True
                    heading_dirty = True
                    logo.invalidate()
                    error_drawn = False''', 'picker.py resize')
    source = replace_once(source,
        '''                            clear_pulse()
                            write_all(master, b"\\x0c")''',
        '''                            clear_pulse()
                            logo.invalidate()
                            write_all(master, b"\\x0c")''', 'picker.py error dismissal')
    source = replace_once(source,
        '''                            clear_pulse()
                            write_all(master, b'\\x0c')''',
        '''                            clear_pulse()
                            logo.invalidate()
                            write_all(master, b'\\x0c')''', 'picker.py help dismissal')
    source = replace_once(source,
        '''                        clear_pulse()
                        pulse_needs_draw = True''',
        '''                        clear_pulse()
                        logo.invalidate()
                        pulse_needs_draw = True''', 'picker.py erasure')
    source = replace_once(source,
        '''                        if not sixel:
                            emit(top_banner_packet(columns, rows, heading_version))
                        heading_dirty = False''',
        '''                        if not sixel:
                            emit(top_banner_packet(columns, rows, heading_version))
                            if graphics:
                                emit(logo.packet(columns, rows,
                                    pixel_width=xp or columns * 8,
                                    pixel_height=yp or rows * 16))
                        heading_dirty = False''', 'picker.py logo packet')
    source = replace_once(source,
        '''                cleanup = clear_pulse() + (decoration.delete() if graphics else '')''',
        '''                cleanup = clear_pulse() + (logo.clear() if graphics else '') + (decoration.delete() if graphics else '')''', 'picker.py cleanup')
    return source


def patch_install(source: str) -> str:
    return replace_once(source,
        '''install -m755 cmdvault "$package_root/cmdvault"''',
        '''install -m755 cmdvault "$package_root/cmdvault"
install -m644 cmdvault.png "$package_root/cmdvault.png"''',
        'install.sh')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, default=Path.cwd())
    parser.add_argument('--dry-run', action='store_true')
    args=parser.parse_args()
    root=args.project.expanduser().resolve()
    paths={
        'cmdvault': patch_cmdvault,
        'src/visuals/kitty.py': patch_kitty,
        'src/visuals/picker.py': patch_picker,
        'install.sh': patch_install,
    }
    if not (root / 'cmdvault.png').is_file():
        parser.error(f'Missing {(root / "cmdvault.png")}; put the approved PNG in the repository root')
    changed={}
    for rel,patcher in paths.items():
        p=root / rel
        original=p.read_text(encoding='utf-8')
        patched=patcher(original)
        if p.suffix == '.py' or rel == 'cmdvault':
            ast.parse(patched,filename=rel)
        if patched != original:
            changed[p]=patched
    print(f'Validated {len(paths)} source files, {len(changed)} need updating.')
    for p in changed: print('  ', p.relative_to(root))
    if args.dry_run:
        return
    # No files are changed until all signatures and Python syntax are validated.
    for p,new in changed.items():
        p.with_name(p.name+'.bak').write_bytes(p.read_bytes())
        p.write_text(new,encoding='utf-8')
    print('Done. Backups saved as *.bak; run ./install.sh to update the installed copy.')

if __name__ == '__main__':
    try: main()
    except (OSError,ValueError,RuntimeError,SyntaxError) as exc:
        print(f'CmdVault logo integration aborted: {exc}', file=sys.stderr)
        sys.exit(1)
