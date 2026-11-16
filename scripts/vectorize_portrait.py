#!/usr/bin/env python3
"""Turn a photo into a layered vector portrait: assets/portrait.svg.

    python scripts/vectorize_portrait.py path/to/photo.jpg

Dev-time tool (needs opencv-python-headless and numpy); the Action never runs it.
The subject is cut out with GrabCut, split into tonal layers, traced into smooth
closed paths, and written as <path data-tone="N"> elements in a 400x400 viewBox.
render_cards.py colours the tones from the brand colour and fades them in one
layer at a time, so the portrait "draws itself".
"""
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets" / "portrait.svg"
SIZE = 400          # viewBox
SCALE = 2           # trace at 2x for smoother curves
TONES = 6           # number of tonal layers (tone 0 is the silhouette's lightest)


def cut_out(img):
    """Foreground mask with GrabCut, seeded with a head-and-shoulders shape."""
    h, w = img.shape[:2]
    s = w / 400
    m = np.full((h, w), cv2.GC_PR_BGD, np.uint8)
    cv2.ellipse(m, (int(205 * s), int(165 * s)), (int(95 * s), int(135 * s)), 0, 0, 360, cv2.GC_PR_FGD, -1)
    cv2.ellipse(m, (int(205 * s), int(165 * s)), (int(55 * s), int(95 * s)), 0, 0, 360, cv2.GC_FGD, -1)
    torso = (np.array([[0, 400], [0, 330], [60, 290], [130, 262], [280, 262], [350, 290], [400, 330], [400, 400]]) * s).astype(np.int32)
    cv2.fillPoly(m, [torso], cv2.GC_PR_FGD)
    cv2.rectangle(m, (0, 0), (w - 1, int(6 * s)), cv2.GC_BGD, -1)
    cv2.rectangle(m, (0, 0), (int(25 * s), int(200 * s)), cv2.GC_BGD, -1)
    cv2.rectangle(m, (int(375 * s), 0), (w, int(200 * s)), cv2.GC_BGD, -1)
    bgd, fgd = np.zeros((1, 65)), np.zeros((1, 65))
    cv2.grabCut(img, m, None, bgd, fgd, 8, cv2.GC_INIT_WITH_MASK)
    fg = np.where((m == 1) | (m == 3), 255, 0).astype(np.uint8)
    fg = cv2.morphologyEx(fg, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    return cv2.morphologyEx(fg, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))


def smooth_path(contour, close=True):
    """Closed polygon -> smooth quadratic path through edge midpoints."""
    p = contour.reshape(-1, 2).astype(float) / SCALE
    n = len(p)
    if n < 3:
        return ""
    mids = [(p[i] + p[(i + 1) % n]) / 2 for i in range(n)]
    d = f"M{mids[-1][0]:.1f} {mids[-1][1]:.1f}"
    for i in range(n):
        d += f"Q{p[i][0]:.1f} {p[i][1]:.1f} {mids[i][0]:.1f} {mids[i][1]:.1f}"
    return d + "Z"


def trace(binary, min_area, eps):
    binary = cv2.GaussianBlur(binary, (0, 0), 2.2)
    binary = (binary > 127).astype(np.uint8) * 255
    cs, _ = cv2.findContours(binary, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
    parts = []
    for c in cs:
        if abs(cv2.contourArea(c)) < min_area:
            continue
        parts.append(smooth_path(cv2.approxPolyDP(c, eps, True)))
    return "".join(parts)


def main(src):
    img = cv2.imread(src)
    if img is None:
        raise SystemExit(f"cannot read {src}")
    side = min(img.shape[:2])                                   # centre square crop
    y0, x0 = (img.shape[0] - side) // 2, (img.shape[1] - side) // 2
    img = cv2.resize(img[y0:y0 + side, x0:x0 + side], (SIZE * SCALE, SIZE * SCALE), interpolation=cv2.INTER_LANCZOS4)

    fg = cut_out(img)
    lab = cv2.cvtColor(cv2.bilateralFilter(img, 9, 40, 9), cv2.COLOR_BGR2LAB)
    lum = cv2.GaussianBlur(lab[:, :, 0], (0, 0), 2.5)

    # Thresholds come from the head, so the dark shirt stays flat instead of being
    # split into noisy bands: everything below the collar is pulled to the darkest tone.
    ys = np.arange(lum.shape[0])[:, None] / SCALE
    head = (fg > 0) & (ys < 275)
    hair = 0.6 + 0.4 * np.clip((ys - 85) / 35, 0, 1)             # calm the busy hair texture
    lum = (lum * hair).astype(np.uint8)
    fade = np.clip((ys - 262) / 40, 0, 1)                       # smooth, so no seam shows
    lum = (lum * (1 - 0.5 * fade * fade * (3 - 2 * fade))).astype(np.uint8)
    cuts = np.quantile(lum[head], [(i + 1) / TONES for i in range(TONES - 1)])   # TONES-1 thresholds
    layers = [(TONES - 1, fg)]                                              # lightest = whole silhouette
    for k in range(TONES - 2, -1, -1):                                      # darker and darker
        layers.append((k, ((lum < cuts[k]) & (fg > 0)).astype(np.uint8) * 255))

    paths = []
    for tone, mask in layers:
        d = trace(mask, min_area=60 * SCALE, eps=1.1)
        if d:
            paths.append(f'<path data-tone="{tone}" fill-rule="evenodd" d="{d}"/>')
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {SIZE} {SIZE}">\n' + "\n".join(paths) + "\n</svg>\n")
    print(f"wrote {OUT.relative_to(ROOT)}: {len(paths)} layers, {OUT.stat().st_size // 1024} KB")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    main(sys.argv[1])
