#!/usr/bin/env python3
"""The screenshot harness pipeline, as a four-step flow.

Drawn top-to-bottom on a 1100px canvas.

The article column is capped at 44rem (704px) and is 356px on a 390px phone. A
figure is always downscaled to that, so legibility depends on the ratio of text
size to canvas width — which makes a *narrower* canvas more readable, not less.
The first version of this figure was 2460px of horizontal flow: at 356px its
labels arrived at 2.6 CSS px, and OCR could recover 14 of 93 characters. Stacking
the steps removes the need for width at all, and every label lands above 10 CSS px.

Run:  python3 scripts/figures/shot_pipeline.py --out blog/assets/shots/pipeline-zh.png
(matplotlib only; no other deps.)
"""
import argparse

import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties, findfont
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch, Rectangle

BG = "#f7f4ee"
CARD = "#ffffff"
BORDER = "#ddd5c7"
INK = "#1b1f24"
MUTED = "#4b5563"
BLUE = "#4f5dd0"
TEAL = "#2fa39a"

W_IN, DPI = 5.5, 200
F_TITLE, F_HEAD, F_BODY = 15, 13, 11.5

STEPS = [
    (BLUE, "场景文件", ["JSON 描述：", "打开哪页、点什么、", "滚多少、何时截图"]),
    (TEAL, "真实 React UI", ["Vite 起 harness", "对着 mock 后端", "渲染桌面/移动端"]),
    (BLUE, "无头 Chrome", ["CDP 驱动:", "tap / scroll / eval", "无需显示器"]),
    (TEAL, "PNG / MP4", ["文档配图", "也是 UI 回归", "测试的基准"]),
]


def cjk(size, weight="normal"):
    """A font that can actually draw the Chinese.

    matplotlib's default DejaVu has no CJK coverage and silently emits tofu boxes,
    so a fallback list is the difference between a figure and a wall of rectangles.
    """
    for path in ("/System/Library/Fonts/Hiragino Sans GB.ttc",
                 "/System/Library/Fonts/STHeiti Medium.ttc",
                 "/System/Library/Fonts/Supplemental/Songti.ttc",
                 "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"):
        try:
            fp = FontProperties(fname=path, size=size, weight=weight)
            if findfont(fp) == path:
                return fp
        except Exception:
            continue
    return FontProperties(size=size, weight=weight)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    margin = 0.30
    head_h, line_h = 0.42, 0.32
    body_h = len(STEPS[0][2]) * line_h + 0.22
    card_h = head_h + body_h
    gap = 0.46
    title_h = 0.92
    h_in = title_h + len(STEPS) * card_h + (len(STEPS) - 1) * gap + 0.30

    fig = plt.figure(figsize=(W_IN, h_in), dpi=DPI)
    fig.patch.set_facecolor(BG)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, W_IN)
    ax.set_ylim(h_in, 0)          # y grows downward: lay the steps out in reading order
    ax.axis("off")
    ax.set_facecolor(BG)

    ax.text(W_IN / 2, 0.40, "没有显示器，也能渲染出真实界面",
            fontproperties=cjk(F_TITLE, "bold"), color=INK,
            ha="center", va="center")

    x0, w = margin, W_IN - 2 * margin
    cx = W_IN / 2
    y = title_h
    for i, (accent, title, lines) in enumerate(STEPS):
        ax.add_patch(FancyBboxPatch((x0, y), w, card_h,
                                    boxstyle="round,pad=0,rounding_size=0.07",
                                    fc=CARD, ec=BORDER, lw=1.2, zorder=2))
        ax.add_patch(FancyBboxPatch((x0, y), w, head_h,
                                    boxstyle="round,pad=0,rounding_size=0.06",
                                    fc=accent, ec="none", zorder=3))
        # Square off the header's lower corners so it reads as a bar, not a tab.
        ax.add_patch(Rectangle((x0, y + head_h - 0.06), w, 0.06,
                               fc=accent, ec="none", zorder=3))
        ax.text(x0 + 0.22, y + head_h / 2, title, fontproperties=cjk(F_HEAD, "bold"),
                color="white", ha="left", va="center", zorder=4)

        for j, line in enumerate(lines):
            ax.text(x0 + 0.22, y + head_h + 0.16 + j * line_h, line,
                    fontproperties=cjk(F_BODY), color=MUTED,
                    ha="left", va="center", zorder=4)

        if i < len(STEPS) - 1:
            ax.add_patch(FancyArrowPatch(
                (cx, y + card_h + 0.06), (cx, y + card_h + gap - 0.06),
                arrowstyle="-|>", mutation_scale=13, color="#8b8578",
                lw=1.8, zorder=3))
        y += card_h + gap

    fig.savefig(args.out, facecolor=BG)
    print(f"wrote {args.out} ({W_IN * DPI:.0f}x{h_in * DPI:.0f})")


if __name__ == "__main__":
    main()
