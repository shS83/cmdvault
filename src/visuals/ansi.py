"""ANSI renderer and fzf adapter, requiring no raster libraries."""
from .renderer import Renderer

def fzf_options(truecolor: bool = True, glow: float = .65) -> list:
    if not truecolor:
        return ["--color=dark,bg:0,fg:15,border:5,hl:13,fg+:15,bg+:0,hl+:13,pointer:13"]
    level = int(110 + glow * 100)
    return [f"--color=dark,bg:#0b0812,fg:#eee8ff,border:#{level:02x}74ef,hl:#d5a0ff,fg+:#ffffff,bg+:#302040,hl+:#efbcff,pointer:#d59bff,prompt:#c68aff,header:#c9b8df"]

class AnsiRenderer(Renderer):
    def draw(self, lines, phase=0):
        if not self.caps.tty:
            self.output.write("\n".join(lines) + "\n")
            return
        w, h = self.width, self.height
        color = "\x1b[38;2;190;120;250m\x1b[48;2;11;8;18m" if self.caps.truecolor else "\x1b[35m"
        rows = []
        for y in range(h):
            if w < 4 or h < 3:
                row = (lines[y] if y < len(lines) else "")[:w]
            elif y in (0, h-1):
                row = ("╭" if y == 0 else "╰") + "─"*(w-2) + ("╮" if y == 0 else "╯")
            else:
                text = lines[y-1] if y-1 < len(lines) else ""
                row = "│" + text[:w-2].ljust(w-2) + "│"
            if self.caps.truecolor:
                purple = int(130 + 90 * (y / max(1, h-1)) * self.config.glow)
                color = f"\x1b[38;2;{purple};120;250m\x1b[48;2;11;8;18m"
            rows.append(f"\x1b[{y+1};1H" + color + row)
        self.emit("".join(rows) + "\x1b[0m")
