#!/usr/bin/env python3
"""Three-platform sandbox comparison figure for the Chinese sandbox post.

Designed for the column it is actually read in. Prose images render at most
704px wide (`--wrap`), so a 2360px canvas is downscaled 3.35x and 26px text
arrives at 7.8px — legible on a desktop monitor, a grey smear on a phone. This
draws at 1400px so the same text lands at 13px.

Layout is a stack of three cards rather than a four-column table: a table that
wide needs the whole canvas for columns and leaves nothing for glyph size.

Run:  python3 scripts/figures/sandbox_platforms.py --out blog/assets/sandbox/compare-zh.png
"""
import argparse

import matplotlib
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties, findfont
from matplotlib.patches import FancyBboxPatch

BG = "#faf7f0"
CARD = "#ffffff"
BORDER = "#e7dfd1"
INK = "#1b1f24"
MUTED = "#6b7280"
MAC = "#3b5bdb"
LINUX = "#0d9488"
WIN = "#b45309"
GOOD = "#1a7f37"
BAD = "#b42318"

W, H = 1400, 1150
DPI = 200


def cjk_font(size, weight="normal"):
    """A font that can actually draw the Chinese, with fallbacks.

    matplotlib's default DejaVu has no CJK coverage and silently renders tofu;
    PingFang.ttc is a collection that PIL cannot open, so Hiragino comes first.
    """
    for path in ("/System/Library/Fonts/Hiragino Sans GB.ttc",
                 "/System/Library/Fonts/STHeiti Medium.ttc",
                 "/System/Library/Fonts/Supplemental/Songti.ttc"):
        try:
            fp = FontProperties(fname=findfont(FontProperties(family="sans-serif"))
                                if False else path, size=size, weight=weight)
            if findfont(fp) == path:
                return fp
        except Exception:
            continue
    return FontProperties(size=size, weight=weight)


def card(ax, y0, height, accent, name, backend, mechanism, boundary, boundary_bad):
    ax.add_patch(FancyBboxPatch(
        (34, y0), W - 68, height, boxstyle="round,pad=0,rounding_size=14",
        fc=CARD, ec=BORDER, lw=1.4, zorder=2))
    # accent spine
    ax.add_patch(FancyBboxPatch(
        (34, y0 + 26), 10, height - 52, boxstyle="round,pad=0,rounding_size=5",
        fc=accent, ec="none", zorder=3))

    ax.text(78, y0 + height / 2, name, fontproperties=cjk_font(15, "bold"),
            color=accent, va="center", ha="left", zorder=4)

    lx, vx = 330, 430
    rows = [("后端", backend, INK), ("机制", mechanism, INK),
            ("读写边界", boundary, BAD if boundary_bad else GOOD)]
    for i, (label, value, color) in enumerate(rows):
        y = y0 + 62 + i * 62
        ax.text(lx, y, label, fontproperties=cjk_font(9, "normal"),
                color=MUTED, va="center", ha="left", zorder=4)
        ax.text(vx, y, value, fontproperties=cjk_font(10.5, "bold"),
                color=color, va="center", ha="left", zorder=4)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    fig = plt.figure(figsize=(W / DPI, H / DPI), dpi=DPI)
    fig.patch.set_facecolor(BG)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, W)
    ax.set_ylim(H, 0)          # y grows downward: lay the cards out top-first
    ax.axis("off")
    ax.set_facecolor(BG)

    ax.text(W / 2, 54, "一个产品承诺，三套操作系统机制",
            fontproperties=cjk_font(15, "bold"), color=INK,
            ha="center", va="center")
    ax.text(W / 2, 106, "同一份规则，在每个平台被翻译成完全不同的东西",
            fontproperties=cjk_font(10.2, "normal"), color="#4b5563",
            ha="center", va="center")

    CH, GAP, TOP = 268, 46, 158
    card(ax, TOP, CH, MAC, "macOS", "Seatbelt", "SBPL 路径规则", "读 + 写 都受控", False)
    card(ax, TOP + CH + GAP, CH, LINUX, "Linux", "Bubblewrap",
         "只读根 + 可写挂载", "读 + 写 都受控", False)
    y3 = TOP + 2 * (CH + GAP)
    card(ax, y3, CH, WIN, "Windows", "RestrictedToken + NTFS ACL",
         "能力 SID 写边界", "只控写 · 读不受控", True)

    fig.savefig(args.out, facecolor=BG)
    print(f"wrote {args.out} ({W}x{H})")


if __name__ == "__main__":
    main()
