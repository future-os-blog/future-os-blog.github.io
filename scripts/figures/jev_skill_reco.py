#!/usr/bin/env python3
"""Figures for the Jev skill-recommendation post.

Three figures, all sized for the column they are read in: the prose is capped at
44rem (704px) and a 390px phone shows 356px, so a figure is always downscaled to
that width and *only the ratio of text size to canvas width* decides legibility.
Every canvas here is 1100px wide (5.5in at 200dpi) with labels at 11-13pt, which
lands at ~10 CSS px on the narrowest screen, per the house rule in
`e2ee_protocol.py`.

  1. fig1-pipeline.png      one request's path: gates -> candidates -> one Jev
                            Choice -> the none gate -> a card or a plain send
  2. fig2-systems.png       quality vs latency vs cost for the four systems,
                            with the run-to-run noise band drawn on it
  3. fig3-desc-knee.png     the description-length sweep: quality flattens at
                            220 chars while the option table keeps costing more

Run:  python3 scripts/figures/jev_skill_reco.py --out-dir blog/assets/skill-reco
(matplotlib only.)
"""
import argparse
import math
import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

INK = "#1b1f24"
MUTED = "#57606a"
LINE = "#9aa4b0"
ACCENT = "#0a6dd6"
ACCENT_SOFT = "#e7f0fb"
GOOD = "#116329"
GOOD_SOFT = "#dafbe1"
WARN = "#9a3412"
WARN_SOFT = "#fdece3"
BOX = "#f6f8fa"
MONO = "monospace"

W_IN, DPI = 5.5, 200          # 1100px wide


def newest_style():
    return {"font.family": "DejaVu Sans", "figure.dpi": DPI}


def blank(w_in, h_in):
    fig = plt.figure(figsize=(w_in, h_in), dpi=DPI)
    ax = fig.add_axes((0, 0, 1, 1))
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 100)
    ax.axis("off")
    return fig, ax


def check_inside(fig, label):
    """Fail loudly if any text leaves the canvas.

    A figure is downscaled to 356px on a phone, so a label that runs off the
    right edge is not a cosmetic problem — it is half a sentence nobody reads.
    Cheap to check, and invisible when you look at the source resolution (the
    same trap the blog's legibility rule describes).
    """
    fig.canvas.draw()
    width_px, height_px = fig.canvas.get_width_height()
    for ax in fig.axes:
        for text in ax.texts:
            bbox = text.get_window_extent(fig.canvas.get_renderer())
            if bbox.x1 > width_px - 2 or bbox.x0 < 2 or bbox.y1 > height_px - 1:
                raise SystemExit(
                    f"{label}: text leaves the canvas "
                    f"(x {bbox.x0:.0f}..{bbox.x1:.0f} of {width_px}, "
                    f"y1 {bbox.y1:.0f} of {height_px}): {text.get_text()[:60]!r}")
    for text in fig.texts:
        bbox = text.get_window_extent(fig.canvas.get_renderer())
        if bbox.x1 > width_px - 2 or bbox.x0 < 2:
            raise SystemExit(
                f"{label}: figure text leaves the canvas: {text.get_text()[:60]!r}")
    # Axis labels and tick labels are not in `ax.texts`, and a rotated y-label
    # running off the left edge is exactly how "first answer correct" became
    # "llse answer correct" the first time this figure was drawn.
    for ax in fig.axes:
        if not ax.axison:
            continue
        artists = [ax.xaxis.label, ax.yaxis.label]
        artists += list(ax.get_xticklabels()) + list(ax.get_yticklabels())
        for artist in artists:
            if not artist.get_text():
                continue
            bbox = artist.get_window_extent(fig.canvas.get_renderer())
            if bbox.x1 > width_px - 2 or bbox.x0 < 2 or bbox.y1 > height_px - 1 \
                    or bbox.y0 < 1:
                raise SystemExit(
                    f"{label}: axis text leaves the canvas "
                    f"(x {bbox.x0:.0f}..{bbox.x1:.0f}, y {bbox.y0:.0f}..{bbox.y1:.0f}): "
                    f"{artist.get_text()[:40]!r}")


def box(ax, x, y, w, h, *, face=BOX, edge=LINE, radius=0.02, lw=1.0):
    patch = FancyBboxPatch(
        (x, y), w, h,
        boxstyle=f"round,pad=0,rounding_size={radius * 100}",
        linewidth=lw, edgecolor=edge, facecolor=face, mutation_aspect=1,
    )
    ax.add_patch(patch)
    return patch


def arrow(ax, x0, y0, x1, y1, *, color=MUTED, lw=1.4, style="-|>", rad=0.0):
    ax.add_patch(FancyArrowPatch(
        (x0, y0), (x1, y1), arrowstyle=style, mutation_scale=11,
        color=color, linewidth=lw, shrinkA=0, shrinkB=0,
        connectionstyle=f"arc3,rad={rad}",
    ))


# ── figure 1: the serving path ───────────────────────────────────────────────

def fig_pipeline(path):
    fig, ax = blank(W_IN, 4.7)
    x, w = 6.0, 88.0

    ax.text(x, 98, "One request, one Jev call", fontsize=14.5, weight="bold",
            color=INK, va="top")
    ax.text(x, 93.4, "everything else here is the client's decision, not the model's",
            fontsize=11, color=MUTED, va="top")

    # 1. the draft and the client's gates
    box(ax, x, 76.5, w, 13.5, face="#ffffff")
    ax.text(x + 2.6, 87.4, "your draft", fontsize=13, weight="bold", color=INK, va="top")
    ax.text(x + 2.6, 83.6, "≥ 30 bytes  ·  ≤ 2 000 chars  ·  no /skill already picked",
            fontsize=11.5, color=MUTED, va="top")
    ax.text(x + 2.6, 80.2, "toggle on  ·  signed in with balance  ·  budget left today",
            fontsize=11.5, color=MUTED, va="top")

    arrow(ax, x + 6, 76.0, x + 6, 71.0)
    ax.text(x + 8.6, 73.5, "a gate fails → the message simply sends (no call is made)",
            fontsize=11, color=MUTED, va="center")

    # 2. candidates
    box(ax, x, 55.5, w, 15.5, face=ACCENT_SOFT, edge=ACCENT)
    ax.text(x + 2.6, 68.4, "candidates = catalogue − installed", fontsize=13,
            weight="bold", color=INK, va="top")
    ax.text(x + 2.6, 64.6, "141 skills today — 15 builtin + 126 third-party",
            fontsize=11.5, color=MUTED, va="top")
    ax.text(x + 2.6, 61.8, "minus whatever is already installed",
            fontsize=11.5, color=MUTED, va="top")
    ax.text(x + 2.6, 59.0, "a candidate is `name` + its description, capped at 220 chars",
            fontsize=11.5, color=MUTED, va="top")

    arrow(ax, x + 6, 55.0, x + 6, 51.5)
    ax.text(x + 8.6, 54.0, "one Choice, one round-trip — ≈ 8.8k tokens, written once",
            fontsize=11, color=MUTED, va="center")

    # 3. the call
    box(ax, x, 22.0, w, 29.0, face="#ffffff", edge=INK, lw=1.4)
    ax.text(x + 2.6, 48.0, "Jev · System One — POST /v1/systemone",
            fontsize=13, weight="bold", color=INK, va="top")
    ax.text(x + 2.6, 44.2, "state: the request   questions: chunk_0 (one Choice)", fontsize=11.5,
            color=MUTED, va="top")
    ax.text(x + 2.6, 40.0,
            "criteria: 141 × (name -> description) + none_of_these",
            fontsize=10.5, color=INK, va="top", family=MONO)
    ax.text(x + 2.6, 35.6,
            "\"The request in `request` needs a skill from `criteria`.\n"
            " Which one, or does none of them help? … Choosing it is\n"
            " a normal answer here, not a fallback.\"",
            fontsize=11, color=MUTED, va="top")
    ax.text(x + 2.6, 26.0,
            "→ one distribution over 142 options, summing to 1",
            fontsize=10.5, color=ACCENT, va="top", weight="bold")

    # 4. two outcomes
    left_x, right_x, ow = x, x + w / 2 + 1.5, w / 2 - 1.5
    box(ax, left_x, 3.0, ow, 15.0, face=GOOD_SOFT, edge=GOOD)
    ax.text(left_x + 2.4, 16.0, "none ≥ 0.15 → nothing fits", fontsize=12,
            weight="bold", color=GOOD, va="top")
    ax.text(left_x + 2.4, 12.0, "no card, no extra call:", fontsize=11, color=MUTED, va="top")
    ax.text(left_x + 2.4, 8.6, "the draft is sent as typed", fontsize=11, color=MUTED, va="top")

    box(ax, right_x, 3.0, ow, 15.0, face=ACCENT_SOFT, edge=ACCENT)
    ax.text(right_x + 2.4, 16.0, "top-1 probability wins", fontsize=12,
            weight="bold", color=ACCENT, va="top")
    ax.text(right_x + 2.4, 12.0, "card above the input:", fontsize=11, color=MUTED, va="top")
    ax.text(right_x + 2.4, 8.6, "“Install and use” or “Ignore”", fontsize=11,
            color=MUTED, va="top")

    arrow(ax, x + 6, 21.5, left_x + ow / 2 - 4, 18.4)
    arrow(ax, x + 6, 21.5, right_x + ow / 2 - 12, 18.4)

    ax.text(x + w, 0.2, "the gate reads the model's own answer, not a score we fit",
            fontsize=10.5, color=MUTED, ha="right", va="bottom")

    fig.savefig(path, facecolor="white")
    check_inside(fig, os.path.basename(path))
    plt.close(fig)


# ── figure 2: quality, latency, cost ─────────────────────────────────────────

# (row label, cost shown under it, first-answer correct %, correct refusal %,
#  latency p50 ms, colour)
SYSTEMS = [
    ("Jev · one call", 90.8, 97.1, 488, 0.0027, ACCENT),
    ("deepseek-flash", 93.8, 97.1, 1747, 0.0249, WARN),
    ("local embeddings", 64.6, 71.4, 15, 0.0000, "#5b616b"),
]


def fig_systems(path):
    """Two aligned panels, one row per system.

    A scatter with four floating labels was tried first and put three labels on
    top of each other: three of the four systems sit within 5% of each other.
    Rows cannot collide — the label lives on the y axis, not in the plot area.
    """
    height = len(SYSTEMS)
    rows = range(height)
    fig = plt.figure(figsize=(W_IN, 3.6), dpi=DPI)
    left = fig.add_axes((0.33, 0.185, 0.28, 0.63))
    right = fig.add_axes((0.655, 0.185, 0.30, 0.63), sharey=left)

    for row, (label, top1, _refusal, ms, cost, color) in zip(rows, SYSTEMS):
        left.barh(row, ms, height=0.5, color=color, alpha=0.85, zorder=3)
        right.barh(row, top1, height=0.5, color=color, alpha=0.85, zorder=3)
        # Inside the bar, not after it: a label that follows the bar end pushes
        # the widest row off the canvas, which is how this figure failed twice.
        right.text(top1 - 2.5, row, f"{top1:.1f}%", fontsize=12, weight="bold",
                   color="white", va="center", ha="right", zorder=4)

    left.set_xscale("log")
    left.set_xlim(8, 4000)
    left.set_xticks([10, 100, 1000])
    left.set_xticklabels(["10 ms", "100", "1 s"], fontsize=11.5)
    left.set_xlabel("latency, one decision (p50)", fontsize=12, color=INK)

    right.set_xlim(0, 125)
    right.set_xticks([0, 50, 100])
    right.set_xticklabels(["0", "50%", "100%"], fontsize=11.5)
    right.set_xlabel("first answer correct", fontsize=12, color=INK)

    left.set_yticks(list(rows))
    left.set_yticklabels([
        f"{label}\n{ms:,} ms · ¥{cost:.4f}" for label, _t, _r, ms, cost, _c in SYSTEMS
    ], fontsize=12.5, color=INK)
    left.invert_yaxis()
    left.set_ylim(height - 0.55, -0.55)

    for ax in (left, right):
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        for side in ("left", "bottom"):
            ax.spines[side].set_color(LINE)
        ax.tick_params(colors=MUTED, length=3)
        ax.grid(axis="x", color="#e7ebf0", linewidth=0.8)
        ax.set_axisbelow(True)
    left.tick_params(axis="y", length=0)

    fig.text(0.30, 0.955, "quality: first answer correct (of 65 answerable)",
             fontsize=10.5, color=MUTED, ha="left", va="top")
    fig.savefig(path, facecolor="white")
    check_inside(fig, os.path.basename(path))
    plt.close(fig)


# ── figure 3: the description-length knee ───────────────────────────────────

# Left panel: batch D, every variant paired inside ONE request (§2.2.6).
# None is 65 nor 100; the sweep is on the 65 answerable questions.
QUALITY = [(110, 57), (150, 58), (180, 57), (190, 60), (220, 60), (240, 61), (256, 61)]
# The independent re-check of 256 chars, from its own request.
RECHECK = (256, 62)
# Right panel: `bench/desc-cost.mjs`, one table per request, so that request's
# input tokens belong entirely to that step.
COST = [(110, 5705), (150, 6945), (220, 8782), (256, 9600), (300, 10500)]


def fig_knee(path):
    fig = plt.figure(figsize=(W_IN, 4.6), dpi=DPI)
    top = fig.add_axes((0.16, 0.555, 0.82, 0.335))
    bottom = fig.add_axes((0.16, 0.115, 0.82, 0.335), sharex=top)

    for ax in (top, bottom):
        ax.axvline(220, color=ACCENT, linewidth=1.1, linestyle=(0, (4, 3)), zorder=1)
    top.text(216, 56.4, "220 = the knee (shipped)", fontsize=10.5, color=ACCENT,
             va="bottom", ha="right")

    xs, ys = zip(*QUALITY)
    top.plot(xs, ys, color=INK, linewidth=1.6, marker="o", markersize=5, zorder=3)
    top.scatter([RECHECK[0]], [RECHECK[1]], color=ACCENT, marker="o", s=52,
                zorder=4, edgecolor="white", linewidth=1.1)
    top.annotate("independent\nre-check: 62", (RECHECK[0], RECHECK[1]),
                 textcoords="offset points", xytext=(-8, 4), fontsize=10.5,
                 color=ACCENT, ha="right", va="bottom")
    top.set_ylim(55.6, 63.6)
    top.set_yticks([56, 58, 60, 62])
    top.set_ylabel("first answer correct\n(of 65)", fontsize=11, color=INK)
    top.set_xticklabels([])

    cx, cy = zip(*COST)
    bottom.plot(cx, cy, color=WARN, linewidth=1.6, marker="s", markersize=5)
    bottom.set_ylim(4200, 12600)
    bottom.set_yticks([5000, 7500, 10000])
    bottom.set_yticklabels(["5k", "7.5k", "10k"])
    bottom.set_ylabel("input tokens / question", fontsize=11, color=INK)
    bottom.set_xlabel("description length in the option table, characters", fontsize=11.5,
                      color=INK)
    bottom.set_xticks([110, 150, 190, 220, 256, 300])
    bottom.set_xticklabels(["110", "150", "190", "220", "256", "300"], fontsize=11)

    for ax in (top, bottom):
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        for side in ("left", "bottom"):
            ax.spines[side].set_color(LINE)
        ax.tick_params(colors=MUTED, length=3, labelsize=11)
        ax.grid(axis="y", color="#e7ebf0", linewidth=0.8)
        ax.set_axisbelow(True)
    top.text(112, 63.3, "quality is flat above 220", fontsize=10.5, color=MUTED, va="top")
    top.text(112, 62.3, "shorter loses answers one at a time", fontsize=10.5, color=MUTED,
             va="top")
    bottom.text(112, 12300, "cost is linear in the description length",
                fontsize=10.5, color=MUTED, va="top")

    fig.savefig(path, facecolor="white")
    check_inside(fig, os.path.basename(path))
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", default="blog/assets/skill-reco")
    args = parser.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)
    plt.rcParams.update(newest_style())
    fig_pipeline(os.path.join(args.out_dir, "fig1-pipeline.png"))
    fig_systems(os.path.join(args.out_dir, "fig2-systems.png"))
    fig_knee(os.path.join(args.out_dir, "fig3-desc-knee.png"))
    for name in ("fig1-pipeline.png", "fig2-systems.png", "fig3-desc-knee.png"):
        full = os.path.join(args.out_dir, name)
        print(f"{name}: {os.path.getsize(full) / 1024:.0f} KB")


if __name__ == "__main__":
    main()
