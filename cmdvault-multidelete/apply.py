#!/usr/bin/env python3
"""Conservative in-place upgrade to Space multi-select + Ctrl-D batch delete."""
from pathlib import Path
import argparse
import ast
import re
import shutil


def transform(source: str) -> str:
    if 'def _cv_selection_indices(' in source:
        raise ValueError('Multi-delete patch appears already installed')
    if source.count('"--no-multi"') != 1:
        raise ValueError('Expected one --no-multi fzf option; source changed')
    source = source.replace('"--no-multi"', '"--multi", "--bind=space:toggle"', 1)
    anchor = 'def pick_loop('
    if source.count(anchor) != 1:
        raise ValueError('Unable to locate pick_loop')
    helper = '''def _cv_selection_indices(rows, entries):
    """Resolve fzf output by stable row index; do not parse displayed commands."""
    selected = []
    for row in rows:
        try:
            index = int(row.split("\\t", 1)[0])
        except (ValueError, IndexError) as error:
            raise ValueError("Invalid picker selection") from error
        if not 0 <= index < len(entries):
            raise ValueError("Invalid picker selection")
        if index not in selected:
            selected.append(index)
    return selected


def _cv_confirm_batch_delete(count, terminal="/dev/tty"):
    """Single acknowledgement for a destructive, possibly multi-command edit."""
    with open(terminal, "w", encoding="utf-8") as output, open(terminal, "r", encoding="utf-8") as source:
        output.write(f"Delete {count} selected command(s) from Bash history and favorites? y/N: ")
        output.flush()
        return source.readline().strip().lower() == "y"


'''
    source = source.replace(anchor, helper+anchor, 1)
    pattern = r'(?m)^([ \t]*)if len\(lines\) != 3 or lines\[1\] not in (\([^\n]*\)):'
    match = re.search(pattern, source)
    if not match:
        raise ValueError('Expected fzf output validation not found')
    indent = match.group(1)
    source = source[:match.start()] + indent + 'if len(lines) < 3 or lines[1] not in '+match.group(2)+':' + source[match.end():]
    if source.count('query, key, row = lines') != 1:
        raise ValueError('Expected fzf output unpack missing')
    source = source.replace('query, key, row = lines', 'query, key = lines[:2]\n'+indent+'selected_rows = lines[2:]\n'+indent+'row = selected_rows[0]', 1)
    begin = source.find('        if key == "ctrl-d":', source.find('def pick_loop('))
    end = source.find('        if key not in ("ctrl-s", "alt-f"):',begin)
    if begin < 0 or end < 0:
        raise ValueError('Ctrl-D branch format changed; no files written')
    replacement = '''        if key == "ctrl-d":
            # With --multi fzf prints marked rows; without marks it returns
            # the current row. Every index refers to the original entries list.
            indices = _cv_selection_indices(selected_rows, entries)
            if not indices:
                notice = "\\nNo commands selected."
                continue
            if not _cv_confirm_batch_delete(len(indices)):
                notice = "\\nDeletion cancelled."
                continue
            target = history_path or Path(os.environ.get("HISTFILE") or Path.home() / ".bash_history")
            deleted_indices = []
            occurrences = 0
            try:
                for selected_index in indices:
                    occurrences += delete_entry(target, entries[selected_index].command)
                    deleted_indices.append(selected_index)
            except (OSError, ValueError) as error:
                notice = "\\nDeletion failed or was partial: " + display(str(error))
            else:
                notice = f"\\nDeleted {len(deleted_indices)} command(s), {occurrences} history occurrence(s)."
            # Remove successfully processed rows in descending index order.
            for selected_index in sorted(deleted_indices, reverse=True):
                entries.pop(selected_index)
            if not entries:
                print("cmdvault: Selected commands deleted; no entries remain.", file=sys.stderr)
                return None
            position = 1
            continue
'''
    source = source[:begin] + replacement + source[end:]
    ast.parse(source)
    return source


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('launcher', type=Path, nargs='?', default=Path('cmdvault'))
    args = parser.parse_args()
    path = args.launcher
    before = path.read_text(encoding='utf-8')
    after = transform(before)
    backup = path.with_name(path.name + '.before-multidelete.bak')
    if backup.exists():
        raise SystemExit(f'Backup exists: {backup}; move it aside before retrying')
    shutil.copy2(path,backup)
    path.write_text(after,encoding='utf-8')
    print(f'Patched {path}; backup: {backup}')


if __name__ == '__main__':
    main()
