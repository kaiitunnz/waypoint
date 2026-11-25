#!/usr/bin/env -S uv run --quiet --with Pillow python3
"""Regenerate web icon assets from the canonical brand sources.

Reads `assets/icons/apple/waypoint-iOS-Default-1024x1024@1x.png` (or `--source`)
and writes the PNGs consumed by the Next.js manifest and Apple touch icon. The
maskable variant is inset to the PWA 80% safe zone on a brand background so
platforms that crop into a circle don't clip the mark.

Reads `assets/icons/waypoint-favicon.svg` and writes favicon assets into
`frontend/public` and `frontend/src/app`, including `favicon.svg`,
`favicon.ico`, and the PNG favicons under `frontend/public/icons/`.

Also mirrors `assets/icons/waypoint.svg` and `assets/icons/waypoint-light.svg`
to `frontend/public/` so the in-app brand mark stays in sync with the canonical
artwork across themes.

Writes the v2 app's icons (`frontend-v2/`) from the same sources with a red
PREVIEW badge, or a red dot on favicons too small for text, so an installed v2
is told apart from v1. The badge text needs a bold sans TrueType font.

Usage:
    scripts/generate_icons.py
    scripts/generate_icons.py --source path/to/1024.png
    scripts/generate_icons.py --check   # verify outputs match source, no write
"""

import argparse
import shutil
import subprocess
import sys
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE = (
    REPO_ROOT / "assets" / "icons" / "apple" / "waypoint-iOS-Default-1024x1024@1x.png"
)
FAVICON_SOURCE = REPO_ROOT / "assets" / "icons" / "waypoint-favicon.svg"
PUBLIC_DIR = REPO_ROOT / "frontend" / "public"
PUBLIC_ICONS = PUBLIC_DIR / "icons"
APP_DIR = REPO_ROOT / "frontend" / "src" / "app"

SVG_SOURCE = REPO_ROOT / "assets" / "icons" / "waypoint.svg"
SVG_LIGHT_SOURCE = REPO_ROOT / "assets" / "icons" / "waypoint-light.svg"
SVG_TARGET = PUBLIC_DIR / "waypoint.svg"
SVG_LIGHT_TARGET = PUBLIC_DIR / "waypoint-light.svg"
FAVICON_SVG_TARGET = PUBLIC_DIR / "favicon.svg"
FAVICON_ICO_TARGET = PUBLIC_DIR / "favicon.ico"
APP_FAVICON_ICO_TARGET = APP_DIR / "favicon.ico"
SHARP_MODULE = REPO_ROOT / "node_modules" / "sharp"

V2_PUBLIC_DIR = REPO_ROOT / "frontend-v2" / "public"
V2_APP_DIR = REPO_ROOT / "frontend-v2" / "src" / "app"
V2_FAVICON_SVG_TARGET = V2_PUBLIC_DIR / "favicon.svg"
V2_FAVICON_ICO_TARGET = V2_APP_DIR / "favicon.ico"
PREVIEW_RED = (229, 72, 77, 255)
PREVIEW_RED_HEX = "#e5484d"
PREVIEW_TEXT = "PREVIEW"
PREVIEW_FONTS = (
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    Path("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
    Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
    Path("/Library/Fonts/Arial Bold.ttf"),
)
# Supersampling factor for the badge, since Pillow's shapes aren't antialiased.
BADGE_SCALE = 4

# Matches `background_color` / `theme_color` in frontend/src/app/manifest.ts.
MASKABLE_BG = (6, 8, 11, 255)
MASKABLE_SAFE_ZONE = 0.80
FAVICON_RASTER_SIZE = 512
FAVICON_ICO_SIZES = ((16, 16), (32, 32), (48, 48))


@dataclass(frozen=True)
class Target:
    path: Path
    size: int
    maskable: bool = False


APP_ICON_TARGETS: tuple[Target, ...] = (
    Target(PUBLIC_ICONS / "apple-touch-icon.png", 180),
    Target(PUBLIC_ICONS / "icon-192.png", 192),
    Target(PUBLIC_ICONS / "icon-512.png", 512),
    Target(PUBLIC_ICONS / "icon-512-maskable.png", 512, maskable=True),
    Target(APP_DIR / "apple-icon.png", 180),
)

FAVICON_PNG_TARGETS: tuple[Target, ...] = (
    Target(PUBLIC_ICONS / "favicon-16.png", 16),
    Target(PUBLIC_ICONS / "favicon-32.png", 32),
    Target(APP_DIR / "icon.png", 64),
)

V2_APP_ICON_TARGETS: tuple[Target, ...] = (
    Target(V2_PUBLIC_DIR / "icons" / "icon-192.png", 192),
    Target(V2_PUBLIC_DIR / "icons" / "icon-512.png", 512),
    Target(V2_PUBLIC_DIR / "icons" / "icon-512-maskable.png", 512, maskable=True),
    Target(V2_APP_DIR / "apple-icon.png", 180),
)

V2_FAVICON_PNG_TARGETS: tuple[Target, ...] = (Target(V2_APP_DIR / "icon.png", 64),)


def render(source: Image.Image, target: Target) -> Image.Image:
    if not target.maskable:
        return source.resize((target.size, target.size), Image.LANCZOS)
    canvas = Image.new("RGBA", (target.size, target.size), MASKABLE_BG)
    inner = round(target.size * MASKABLE_SAFE_ZONE)
    icon = source.resize((inner, inner), Image.LANCZOS)
    offset = ((target.size - inner) // 2, (target.size - inner) // 2)
    canvas.alpha_composite(icon, offset)
    return canvas


def badge_font(size: int) -> ImageFont.FreeTypeFont:
    for path in PREVIEW_FONTS:
        if path.is_file():
            return ImageFont.truetype(str(path), size)
    raise RuntimeError("no bold sans font found for the PREVIEW badge")


def with_preview_badge(image: Image.Image, *, maskable: bool) -> Image.Image:
    """A red PREVIEW pill across the bottom, inside the maskable safe circle
    when the icon is maskable."""
    size = image.size[0] * BADGE_SCALE
    width = size * (0.50 if maskable else 0.72)
    height = size * (0.12 if maskable else 0.17)
    bottom = size * (0.80 if maskable else 0.92)
    left = (size - width) / 2
    box = (left, bottom - height, left + width, bottom)
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    draw.rounded_rectangle(box, radius=height / 2, fill=PREVIEW_RED)
    draw.text(
        ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2),
        PREVIEW_TEXT,
        font=badge_font(round(height * 0.58)),
        fill=(255, 255, 255, 255),
        anchor="mm",
    )
    badged = image.copy()
    badged.alpha_composite(layer.resize(image.size, Image.LANCZOS))
    return badged


def with_preview_dot(image: Image.Image) -> Image.Image:
    """A red dot in the bottom-right corner, ringed in the brand background."""
    size = image.size[0] * BADGE_SCALE
    radius = size * 0.20
    ring = size * 0.05
    center = size - radius - ring
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    outer = radius + ring
    draw.ellipse(
        (center - outer, center - outer, center + outer, center + outer),
        fill=MASKABLE_BG,
    )
    draw.ellipse(
        (center - radius, center - radius, center + radius, center + radius),
        fill=PREVIEW_RED,
    )
    badged = image.copy()
    badged.alpha_composite(layer.resize(image.size, Image.LANCZOS))
    return badged


def svg_with_preview_dot(svg: bytes) -> bytes:
    """The favicon SVG with the same corner dot, sized to its viewBox."""
    text = svg.decode("utf-8")
    view_box = text.split('viewBox="', 1)[1].split('"', 1)[0].split()
    size = float(view_box[2])
    radius = size * 0.20
    ring = size * 0.05
    center = size - radius - ring
    dot = (
        f'<circle cx="{center:g}" cy="{center:g}" r="{radius + ring:g}" '
        f'fill="rgb{MASKABLE_BG[:3]}"/>'
        f'<circle cx="{center:g}" cy="{center:g}" r="{radius:g}" '
        f'fill="{PREVIEW_RED_HEX}"/>\n'
    )
    head, _, tail = text.rpartition("</svg>")
    return f"{head}{dot}</svg>{tail}".encode()


def png_bytes(image: Image.Image) -> bytes:
    buf = BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def ico_bytes(image: Image.Image) -> bytes:
    buf = BytesIO()
    image.save(buf, format="ICO", sizes=FAVICON_ICO_SIZES)
    return buf.getvalue()


def render_svg(source: Path, size: int) -> Image.Image:
    node = shutil.which("node")
    if node is None:
        raise RuntimeError("node is required to rasterize SVG favicons")
    if not SHARP_MODULE.exists():
        raise RuntimeError(f"sharp module not found: {SHARP_MODULE}")

    script = (
        "const sharp=require(process.argv[1]);"
        "sharp(process.argv[2])"
        ".resize(Number(process.argv[3]), Number(process.argv[3]))"
        ".png()"
        ".toBuffer()"
        ".then((buf)=>process.stdout.write(buf))"
        ".catch((err)=>{console.error(err);process.exit(1);});"
    )
    result = subprocess.run(
        [node, "-e", script, str(SHARP_MODULE), str(source), str(size)],
        check=True,
        capture_output=True,
    )
    return Image.open(BytesIO(result.stdout)).convert("RGBA")


def sync_bytes(target: Path, content: bytes, *, label: str, check: bool) -> bool:
    rel = target.relative_to(REPO_ROOT)
    if check:
        existing = target.read_bytes() if target.is_file() else b""
        if existing != content:
            print(f"drift: {rel}")
            return True
        return False

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    print(f"wrote: {rel} ({label})")
    return False


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
    parser.add_argument(
        "--favicon-source",
        type=Path,
        default=FAVICON_SOURCE,
        help=f"favicon SVG source (default: {FAVICON_SOURCE.relative_to(REPO_ROOT)})",
    )
    parser.add_argument(
        "--light-source",
        type=Path,
        default=SVG_LIGHT_SOURCE,
        help=f"light-theme SVG source (default: {SVG_LIGHT_SOURCE.relative_to(REPO_ROOT)})",
    )
    args = parser.parse_args()

    if not args.source.is_file():
        print(f"error: source not found: {args.source}", file=sys.stderr)
        return 2
    if not args.favicon_source.is_file():
        print(
            f"error: favicon source not found: {args.favicon_source}", file=sys.stderr
        )
        return 2
    if not args.light_source.is_file():
        print(f"error: source not found: {args.light_source}", file=sys.stderr)
        return 2

    app_source = Image.open(args.source).convert("RGBA")
    if app_source.size != (1024, 1024):
        print(
            f"warning: source is {app_source.size[0]}x{app_source.size[1]}; expected 1024x1024",
            file=sys.stderr,
        )
    favicon_source = render_svg(args.favicon_source, FAVICON_RASTER_SIZE)

    drift: list[Path] = []
    for target in APP_ICON_TARGETS:
        rendered = png_bytes(render(app_source, target))
        if sync_bytes(
            target.path,
            rendered,
            label=f"{target.size}x{target.size}",
            check=args.check,
        ):
            drift.append(target.path)
    for target in FAVICON_PNG_TARGETS:
        rendered = png_bytes(render(favicon_source, target))
        if sync_bytes(
            target.path,
            rendered,
            label=f"{target.size}x{target.size}",
            check=args.check,
        ):
            drift.append(target.path)

    if sync_bytes(
        FAVICON_ICO_TARGET,
        ico_bytes(favicon_source),
        label="ico",
        check=args.check,
    ):
        drift.append(FAVICON_ICO_TARGET)
    if sync_bytes(
        APP_FAVICON_ICO_TARGET,
        ico_bytes(favicon_source),
        label="ico",
        check=args.check,
    ):
        drift.append(APP_FAVICON_ICO_TARGET)

    favicon_svg_bytes = args.favicon_source.read_bytes()
    if sync_bytes(
        FAVICON_SVG_TARGET,
        favicon_svg_bytes,
        label="svg",
        check=args.check,
    ):
        drift.append(FAVICON_SVG_TARGET)
    if sync_bytes(
        SVG_LIGHT_TARGET,
        args.light_source.read_bytes(),
        label="svg",
        check=args.check,
    ):
        drift.append(SVG_LIGHT_TARGET)

    if SVG_SOURCE.is_file():
        svg_bytes = SVG_SOURCE.read_bytes()
        if sync_bytes(SVG_TARGET, svg_bytes, label="svg", check=args.check):
            drift.append(SVG_TARGET)
    else:
        print(f"warning: svg source not found: {SVG_SOURCE}", file=sys.stderr)

    for target in V2_APP_ICON_TARGETS:
        rendered = png_bytes(
            with_preview_badge(render(app_source, target), maskable=target.maskable)
        )
        if sync_bytes(
            target.path,
            rendered,
            label=f"v2 {target.size}x{target.size}",
            check=args.check,
        ):
            drift.append(target.path)
    for target in V2_FAVICON_PNG_TARGETS:
        rendered = png_bytes(with_preview_dot(render(favicon_source, target)))
        if sync_bytes(
            target.path,
            rendered,
            label=f"v2 {target.size}x{target.size}",
            check=args.check,
        ):
            drift.append(target.path)
    if sync_bytes(
        V2_FAVICON_ICO_TARGET,
        ico_bytes(with_preview_dot(favicon_source)),
        label="v2 ico",
        check=args.check,
    ):
        drift.append(V2_FAVICON_ICO_TARGET)
    if sync_bytes(
        V2_FAVICON_SVG_TARGET,
        svg_with_preview_dot(favicon_svg_bytes),
        label="v2 svg",
        check=args.check,
    ):
        drift.append(V2_FAVICON_SVG_TARGET)

    if args.check and drift:
        print(
            f"\n{len(drift)} icon(s) out of date — rerun without --check",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
