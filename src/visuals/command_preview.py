"""Safe, read-only full-command fzf preview."""
import json
import sys
from pathlib import Path


def preview(commands_file: str, selected_row: str) -> str:
    """Resolve the selected fzf row without executing or altering the command."""
    index_text = selected_row.split("\t", 1)[0].strip()
    if not index_text.isascii() or not index_text.isdecimal():
        raise ValueError("Selected row has no valid index")
    commands = json.loads(Path(commands_file).read_text(encoding="utf-8"))
    index = int(index_text)
    if not isinstance(commands, list) or not 0 <= index < len(commands):
        raise ValueError("Selected row index out of range")
    item = commands[index]
    command = item.get("command") if isinstance(item, dict) else item
    if not isinstance(command, str):
        raise ValueError("Command is not text")
    return command


def main():
    if len(sys.argv) != 3:
        return 2
    try:
        commands = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
        index = int(sys.argv[2].split("\t", 1)[0].strip())
        if not isinstance(commands, list) or not 0 <= index < len(commands):
            return 1
        item = commands[index]
        if isinstance(item, str):  # Backward compatible with existing preview files.
            command, hosts = item, []
        elif isinstance(item, dict):
            command, hosts = item.get("command"), item.get("hosts", [])
        else:
            return 1
        if not isinstance(command, str) or not isinstance(hosts, list):
            return 1
        palette = {
            "ALPHATAURI": "0;229;255",
            "EPSILONTAURI": "255;79;216",
            "THETATAURI": "184;255;77",
            "OUROBOROS": "179;136;255",
        }
        # Preview starts beside the graphic at the top of the picker.
        print("\n\n", end="")
        labels = []
        for host in dict.fromkeys(hosts):
            if not isinstance(host, str):
                continue
            name = host.upper()
            # Escape terminal control codes from any untrusted hostname metadata.
            if not name or len(name) > 64 or not all(c.isalnum() or c in "-_" for c in name):
                continue
            shade = palette.get(name, "255;194;102")
            labels.append(f"\x1b[38;2;{shade}m[{name}]\x1b[0m")
        if labels:
            print("".join(labels))
            print()
        print(command)
    except (OSError, ValueError, IndexError, TypeError) as exc:
        print(f'Preview unavailable: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
