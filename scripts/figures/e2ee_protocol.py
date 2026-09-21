#!/usr/bin/env python3
"""Protocol figure for the phone-desktop E2EE post.

Two panels, stacked: the pairing/handshake sequence, then the binary record
layout each application message is wrapped in.

**Sized for the phone, which is the binding constraint.** The article column is
capped at 44rem (704px) on desktop and, with 1.05rem of padding each side, is
356px on a 390px phone. A figure is always downscaled to that width, so the only
thing that decides whether its text is readable is the ratio of text size to
canvas width — not the canvas's absolute size, and not its resolution.

That means a *narrower* canvas is more legible, which is the opposite of the
usual instinct. The first version drew on 2280px to fit two panels side by side;
at 356px the 8pt labels arrived at 3.6 CSS px and were unreadable. This draws at
1100px with 11.5pt labels, which is 10.4 CSS px on a phone and 20px on desktop.

Panels are stacked rather than side by side for the same reason: two panels
sharing one row halve the width each gets, and the record panel's footnotes
(1100px of text at readable size) then have nowhere to go but a second line.

Run:  python3 scripts/figures/e2ee_protocol.py --out blog/assets/e2ee/protocol.png
(matplotlib only; no other deps.)
"""
import argparse

import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

INK = "#1b1f24"
MUTED = "#57606a"
LINE = "#9aa4b0"
ACCENT = "#0a6dd6"
ACCENT_SOFT = "#e7f0fb"
GOOD = "#116329"
GOOD_SOFT = "#dafbe1"
BOX = "#f6f8fa"
MONO = "monospace"

W_IN, DPI = 5.5, 200          # 1100px wide
HS_H, REC_H = 5.5, 3.4        # panel heights in inches

F_TITLE, F_PANEL = 15, 14
F_PHASE, F_LABEL, F_NOTE = 11.5, 11.5, 11
F_BOX, F_DESC = 13, 10.5

# Vertical offsets from a message's arrow, in axis units (panels are ylim 0-100).
LBL_DY, NOTE_DY = 1.6, 1.8


def style_ax(ax):
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 100)
    ax.axis("off")


def lifeline(ax, x, label, sub):
    ax.text(x, 98, label, ha="center", va="top", fontsize=F_LABEL, color=INK,
            weight="bold")
    ax.text(x, 93.5, sub, ha="center", va="top", fontsize=F_DESC, color=MUTED)
    ax.plot([x, x], [2.5, 91.5], color=LINE, lw=1.2, ls=(0, (4, 3)), zorder=1)


def message(ax, x1, x2, y, text, color=INK, solid=True, note=None, note_color=MUTED):
    ax.add_patch(
        FancyArrowPatch((x1, y), (x2, y), arrowstyle="-|>", mutation_scale=14,
                        color=color, lw=1.7,
                        linestyle="-" if solid else (0, (5, 3)), zorder=3))
    mid = (x1 + x2) / 2
    ax.text(mid, y + LBL_DY, text, ha="center", va="bottom", fontsize=F_LABEL,
            color=color)
    if note:
        ax.text(mid, y - NOTE_DY, note, ha="center", va="top", fontsize=F_NOTE,
                color=note_color, style="italic")


def phase(ax, x0, x1, y, text):
    ax.text((x0 + x1) / 2, y, text, ha="center", va="center", fontsize=F_PHASE,
            color=MUTED, weight="bold")


def handshake(ax):
    """Sequence panel.

    Row spacing is arithmetic, not taste. A message at `y` paints its label up
    from y+LBL_DY and its note down from y-NOTE_DY, so consecutive messages need
    label height + note height + both offsets between them, and a phase label
    needs its own half-height plus the label height above the next message.
    Sit closer than that and two strings share a baseline band and the glyphs
    interleave — invisible at source resolution, obvious at 356px.
    """
    style_ax(ax)
    ax.text(50, 104, "Pairing and handshake", ha="center", va="bottom",
            fontsize=F_PANEL, color=INK, weight="bold")

    PX, DX = 18, 82
    lifeline(ax, PX, "Mobile", "phone")
    lifeline(ax, DX, "Desktop", "tools + files")

    ax.add_patch(FancyBboxPatch((26, 86.5), 48, 6.2, boxstyle="round,pad=0.3",
                                fc=ACCENT_SOFT, ec=ACCENT, lw=1.3, zorder=2))
    ax.text(50, 89.6, "invitation (QR / paste)  ·  5 min, one use", ha="center",
            va="center", fontsize=F_DESC, color=ACCENT, zorder=3)

    phase(ax, PX, DX, 79.5, "first pairing — Noise_XXpsk0")
    message(ax, PX, DX, 72, "eph + identity",
            note="checks remote static key against the scanned key")
    message(ax, DX, PX, 62, "eph + identity + PSK proof")
    message(ax, PX, DX, 52, "finish  ·  PSK verified, key bound", color=GOOD,
            note="desktop deletes the PSK — the invitation is now spent")

    phase(ax, PX, DX, 45, "readiness handoff")
    message(ax, DX, PX, 38, "encrypted handshake-confirm", solid=False)
    message(ax, PX, DX, 28, "encrypted secure_ready  →  candidate committed",
            color=GOOD, note="only now can commands and files flow")

    phase(ax, PX, DX, 21, "every later reconnect — Noise_IK")
    message(ax, PX, DX, 14, "known-identity handshake",
            note="fresh ephemeral keys each time")
    message(ax, DX, PX, 4, "server-supplied keys never re-accepted", solid=False)


def record_layout(ax):
    style_ax(ax)
    ax.text(50, 105, "Every application record", ha="center", va="bottom",
            fontsize=F_PANEL, color=INK, weight="bold")
    ax.text(50, 99.5, "ChaCha20-Poly1305, two directional keys from Noise Split",
            ha="center", va="bottom", fontsize=F_DESC, color=MUTED)

    rows = [
        ("0", "4", "FRE2", "magic", BOX, INK),
        ("4", "16", "handshake hash", "binds to this channel", ACCENT_SOFT, ACCENT),
        ("20", "8", "sequence", "little-endian, monotonic", ACCENT_SOFT, ACCENT),
        ("28", "…", "ciphertext + 16-byte AEAD tag", "the command / reply / chunk",
         GOOD_SOFT, GOOD),
    ]
    top, rh = 90, 19
    for i, (off, nbytes, name, desc, fc, ec) in enumerate(rows):
        y = top - i * rh
        ax.add_patch(FancyBboxPatch((11, y - rh + 1.5), 78, rh - 2.5,
                                    boxstyle="round,pad=0.2", fc=fc, ec=ec,
                                    lw=1.3, zorder=2))
        ax.text(16, y - rh / 2 + 3.0, name, ha="left", va="center",
                fontsize=F_BOX, color=INK, weight="bold", family=MONO, zorder=3)
        ax.text(85.5, y - rh / 2 + 3.0, f"{nbytes} B", ha="right", va="center",
                fontsize=F_DESC, color=MUTED, zorder=3)
        ax.text(16, y - rh + 5.0, desc, ha="left", va="center",
                fontsize=F_DESC, color=MUTED, zorder=3)
        ax.text(9, y - rh / 2 + 3.0, off, ha="right", va="center",
                fontsize=F_DESC, color=MUTED, family=MONO, zorder=3)

    # No footnotes. The three things worth saying about this layout — the subject
    # is authenticated as additional data, a reply is bound to its request, and
    # authentication precedes decoding — are already a bulleted list in the prose
    # directly above. Repeating them here cost six lines of 10pt text in the
    # densest corner of the figure; dropping them let every remaining label grow.


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    fig = plt.figure(figsize=(W_IN, HS_H + REC_H + 0.35), dpi=DPI)
    fig.patch.set_facecolor("white")

    total = HS_H + REC_H + 0.35
    handshake(fig.add_axes([0.02, (REC_H + 0.30) / total, 0.96, HS_H / total]))
    record_layout(fig.add_axes([0.02, 0.02 / total, 0.96, REC_H / total]))

    fig.savefig(args.out, bbox_inches="tight", facecolor="white")
    print("wrote", args.out)


if __name__ == "__main__":
    main()
