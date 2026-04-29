#!/usr/bin/env -S uv run --quiet --with Pillow python3
"""Regenerate web icon assets from the iOS Default source.

Reads `assets/icons/apple/waypoint-iOS-Default-1024x1024@1x.png` (or `--source`)
and writes the PNGs consumed by the Next.js manifest, Apple touch icon, and
favicons. The maskable variant is inset to the PWA 80% safe zone on a brand
background so platforms that crop into a circle don't clip the mark. Also
mirrors `assets/icons/waypoint.svg` to `frontend/public/waypoint.svg` so the
in-app brand mark stays in sync with the canonical artwork.

Usage:
    scripts/generate_icons.py
    scripts/generate_icons.py --source path/to/1024.png
    scripts/generate_icons.py --check   # verify outputs match source, no write
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE = (
    REPO_ROOT / "assets" / "icons" / "apple" / "waypoint-iOS-Default-1024x1024@1x.png"
)
PUBLIC_DIR = REPO_ROOT / "frontend" / "public"
PUBLIC_ICONS = PUBLIC_DIR / "icons"
APP_DIR = REPO_ROOT / "frontend" / "src" / "app"

SVG_SOURCE = REPO_ROOT / "assets" / "icons" / "waypoint.svg"
SVG_TARGET = PUBLIC_DIR / "waypoint.svg"

# Matches `background_color` / `theme_color` in frontend/src/app/manifest.ts.
MASKABLE_BG = (6, 8, 11, 255)
MASKABLE_SAFE_ZONE = 0.80


@dataclass(frozen=True)
class Target:
    path: Path
    size: int
    maskable: bool = False


TARGETS: tuple[Target, ...] = (
    Target(PUBLIC_ICONS / "favicon-16.png", 16),
    Target(PUBLIC_ICONS / "favicon-32.png", 32),
    Target(PUBLIC_ICONS / "apple-touch-icon.png", 180),
    Target(PUBLIC_ICONS / "icon-192.png", 192),
    Target(PUBLIC_ICONS / "icon-512.png", 512),
    Target(PUBLIC_ICONS / "icon-512-maskable.png", 512, maskable=True),
    Target(APP_DIR / "icon.png", 64),
    Target(APP_DIR / "apple-icon.png", 180),
)


def render(source: Image.Image, target: Target) -> Image.Image:
    if not target.maskable:
        return source.resize((target.size, target.size), Image.LANCZOS)
    canvas = Image.new("RGBA", (target.size, target.size), MASKABLE_BG)
    inner = round(target.size * MASKABLE_SAFE_ZONE)
    icon = source.resize((inner, inner), Image.LANCZOS)
    offset = ((target.size - inner) // 2, (target.size - inner) // 2)
    canvas.alpha_composite(icon, offset)
    return canvas


def png_bytes(image: Image.Image) -> bytes:
    from io import BytesIO

    buf = BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        default=DEFAULT_SOURCE,
        help=f"1024x1024 source PNG (default: {DEFAULT_SOURCE.relative_to(REPO_ROOT)})",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Compare on-disk targets to freshly rendered output without writing.",
    )
    args = parser.parse_args()

    if not args.source.is_file():
        print(f"error: source not found: {args.source}", file=sys.stderr)
        return 2

    source = Image.open(args.source).convert("RGBA")
    if source.size != (1024, 1024):
        print(
            f"warning: source is {source.size[0]}x{source.size[1]}; expected 1024x1024",
            file=sys.stderr,
        )

    drift: list[Path] = []
    for target in TARGETS:
        rendered = png_bytes(render(source, target))
        rel = target.path.relative_to(REPO_ROOT)
        if args.check:
            existing = target.path.read_bytes() if target.path.is_file() else b""
            if existing != rendered:
                drift.append(target.path)
                print(f"drift: {rel}")
            continue
        target.path.parent.mkdir(parents=True, exist_ok=True)
        target.path.write_bytes(rendered)
        print(f"wrote: {rel} ({target.size}x{target.size})")

    if SVG_SOURCE.is_file():
        svg_bytes = SVG_SOURCE.read_bytes()
        svg_rel = SVG_TARGET.relative_to(REPO_ROOT)
        if args.check:
            existing = SVG_TARGET.read_bytes() if SVG_TARGET.is_file() else b""
            if existing != svg_bytes:
                drift.append(SVG_TARGET)
                print(f"drift: {svg_rel}")
        else:
            SVG_TARGET.parent.mkdir(parents=True, exist_ok=True)
            SVG_TARGET.write_bytes(svg_bytes)
            print(f"wrote: {svg_rel} (svg)")
    else:
        print(f"warning: svg source not found: {SVG_SOURCE}", file=sys.stderr)

    if args.check and drift:
        print(f"\n{len(drift)} icon(s) out of date — rerun without --check", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
