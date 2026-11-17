"""Dependency-free tests for safe source transformations; no terminal required."""
import ast
import importlib.util
from pathlib import Path
import tempfile
import subprocess
import sys

SCRIPT = Path(__file__).with_name('repair_cmdvault_header.py')
spec = importlib.util.spec_from_file_location('header', SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

MAIN_OLD = '''from pathlib import Path
import shutil
import sys
import os

def f():
\tbackend = "kitty"
\tneon_picker = backend == "kitty" and sys.stdin.isatty()
\tif neon_picker:
\t\tdimensions = shutil.get_terminal_size((80, 24))
\t\tneon_picker = dimensions.columns >= 32 and dimensions.lines >= 10
\tterminal_picker = True
\tdef invoke(argv):
\t\tif terminal_picker:
\t\t\targv += ["--no-clear", "--height=100%", ("--margin=1,2,0,2" if backend == "sixel" else "--margin=1,0,0,0")]
'''
PICKER = r'''def pulse_label_packet(columns, rows, center_y, ch):
    row = max(2, min(rows - 2, round(center_y / ch)))
    return row

def top_banner_packet(columns, rows, heading_version):
    return "HELP"

def run_picker():
    if heading_dirty:
        if not sixel:
            emit(top_banner_packet(columns, rows, heading_version))
            if graphics:
                emit(logo.packet(columns, rows,
                    pixel_width=xp or columns * 8,
                    pixel_height=yp or rows * 16))
        heading_dirty = False
'''
KITTY = '''class KittyLogoOverlay:
    pass

class KittyPulseOverlay:
    def packet(self, ph, margin, orb_px):
        top_px = max(0, ph - margin - orb_px)
        return top_px
'''

with tempfile.TemporaryDirectory() as t:
    root = Path(t)
    (root / 'src/visuals').mkdir(parents=True)
    (root/'cmdvault.png').write_bytes(b'PNG-placeholder-for-source-patching-only')
    (root/'cmdvault').write_text(MAIN_OLD)
    (root/'src/visuals/picker.py').write_text(PICKER)
    (root/'src/visuals/kitty.py').write_text(KITTY)

    original_logo = (root/'cmdvault.png').read_bytes()
    for cmd in [('--dry-run',), ()]:
        res = subprocess.run([sys.executable, str(SCRIPT), '--project', str(root), *cmd],
                             capture_output=True, text=True)
        assert res.returncode == 0, res.stderr
        if cmd:
            assert (root/'cmdvault').read_text() == MAIN_OLD
    a=(root/'cmdvault').read_text()
    b=(root/'src/visuals/picker.py').read_text()
    c=(root/'src/visuals/kitty.py').read_text()
    assert 'logo_header_rows = (6 if neon_picker' in a
    assert 'f"--margin={logo_header_rows},0,0,0"' in a
    assert 'row = max(2, min(rows - 1, round(center_y / ch) + 1))' in b
    assert 'emit(top_banner_packet(' not in b
    assert 'emit(logo.packet(columns, rows' in b
    assert 'top_px = max(0, ph - margin - orb_px + 10)' in c
    assert (root/'cmdvault.png').read_bytes() == original_logo
    assert mod.patch_main(a) == a
    assert mod.patch_picker(b) == b
    assert mod.patch_kitty(c) == c
    for rel in ('cmdvault','src/visuals/picker.py','src/visuals/kitty.py'):
        ast.parse((root/rel).read_text())
    # Verify updated code shifts the orb exactly ten physical pixels.
    ns={}
    exec(c,ns)
    orb=ns['KittyPulseOverlay']()
    assert orb.packet(1100,104,104) -  (1100-104-104)==10

# Verify existing integrated top margin isn't duplicated.
main_integrated = MAIN_OLD.replace(mod.OLD_PICKER,mod.NEW_PICKER).replace(mod.OLD_MARGIN,mod.NEW_MARGIN)
assert mod.patch_main(main_integrated) == main_integrated

print('PASS: restored margin, hidden banner, READY original row, orb +10 px, idempotence, syntax, PNG unchanged')
