"""Command-line interface for seamproof.

Usage:
    seamproof fix <file-or-folder> --out <dir> [--score-only]
                  [--no-detrend] [--band N]

A folder is treated as one or more PBR sets: files whose names share a
base name and differ only by a map suffix (``_albedo``, ``_normal``,
``_roughness``, ``_height`` ...) form one set and are fixed with one
shared geometry.  See README for the full suffix convention.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
from PIL import Image

from . import core

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tga", ".bmp", ".webp", ".tif", ".tiff"}

# Suffix (lowercase, without extension) -> map type.  Longest match wins.
SUFFIX_TYPES = {
    "_basecolor": "albedo",
    "_base_color": "albedo",
    "_albedo": "albedo",
    "_diffuse": "albedo",
    "_color": "albedo",
    "_col": "albedo",
    "_normalgl": "normal",
    "_normaldx": "normal",
    "_normal": "normal",
    "_nrm": "normal",
    "_roughness": "roughness",
    "_rough": "roughness",
    "_displacement": "height",
    "_height": "height",
    "_disp": "height",
    "_bump": "height",
    "_metallic": "other",
    "_metalness": "other",
    "_ao": "other",
    "_occlusion": "other",
}


class UserError(Exception):
    """A problem the user can fix; printed as one line, no traceback."""


def parse_map_name(filename: str) -> tuple[str, str]:
    """Split a filename into (base name, map type).

    ``rock_normal.png`` -> (``rock``, ``normal``).  A file with no known
    suffix is its own base with type ``single``.
    """
    stem = Path(filename).stem
    low = stem.lower()
    for suffix in sorted(SUFFIX_TYPES, key=len, reverse=True):
        if low.endswith(suffix) and len(stem) > len(suffix):
            return stem[: -len(suffix)], SUFFIX_TYPES[suffix]
    return stem, "single"


def load_image(path: Path):
    """Load HxWxC floats in 0–255, optional alpha, and the output mode.

    The I;16 output mode preserves the depth of a 16-bit grayscale PNG.
    """
    try:
        with path.open("rb") as stream:
            header = stream.read(26)
            stream.seek(0)
            img = Image.open(stream)
            img.load()
    except Exception:
        raise UserError(f"not a readable image: {path.name}")
    # Pillow exposes 16-bit PNG color as RGB/RGBA after reducing it to 8 bits.
    # The IHDR bit-depth and color-type bytes still identify the original data.
    if img.format == "PNG" and len(header) >= 26 and header[24] == 16:
        if header[25] != 0 or "transparency" in img.info or path.suffix.lower() != ".png":
            raise UserError(f"unsupported 16-bit PNG: {path.name} (expected grayscale PNG without transparency)")
        return np.asarray(img, dtype=np.float64)[:, :, None] / 257, None, "I;16"
    if img.mode in ("I", "F") or img.mode.startswith("I;16") or (
        img.format == "TIFF" and any(depth > 8 for depth in img.tag_v2.get(258, ()))
    ):
        raise UserError(f"unsupported high-bit-depth image: {path.name} (use a grayscale 16-bit PNG)")
    if img.mode == "RGBA":
        arr = np.asarray(img).astype(np.float64)
        return arr[:, :, :3].copy(), arr[:, :, 3].copy(), "RGBA"
    if img.mode == "LA":
        arr = np.asarray(img).astype(np.float64)
        return arr[:, :, 0:1].copy(), arr[:, :, 1].copy(), "LA"
    if img.mode == "L":
        arr = np.asarray(img, dtype=np.float64)
        return arr[:, :, None].copy(), None, "L"
    arr = np.asarray(img.convert("RGB")).astype(np.float64)
    return arr.copy(), None, "RGB"


def save_image(path: Path, arr: np.ndarray, alpha, mode: str) -> None:
    if mode == "I;16":
        values = np.clip(np.rint(arr[:, :, 0] * 257), 0, 65535).astype("<u2")
        Image.fromarray(values).save(path, format="PNG")
        return
    a = np.clip(np.rint(arr), 0, 255).astype(np.uint8)
    if mode == "L":
        img = Image.fromarray(a[:, :, 0], mode="L")
    elif mode in ("RGBA", "LA"):
        alpha_u8 = np.clip(np.rint(alpha), 0, 255).astype(np.uint8)
        if mode == "RGBA":
            rgba = np.dstack([a, alpha_u8])
            img = Image.fromarray(rgba, mode="RGBA")
        else:
            la = np.dstack([a[:, :, 0], alpha_u8])
            img = Image.fromarray(la, mode="LA")
    else:
        img = Image.fromarray(a, mode="RGB")
    img.save(path)


def group_inputs(paths: list[Path]) -> dict[str, list[tuple[Path, str]]]:
    """Group files into sets by base name: {base: [(path, map_type)]}."""
    groups: dict[str, list[tuple[Path, str]]] = {}
    for p in paths:
        base, mtype = parse_map_name(p.name)
        groups.setdefault(base, []).append((p, mtype))
    return groups


def collect_inputs(target: Path) -> tuple[dict, list[str]]:
    """Return (groups, warnings) for a file or folder target."""
    warnings: list[str] = []
    if not target.exists():
        raise UserError(f"no such file or folder: {target}")
    if target.is_file():
        if target.suffix.lower() not in IMAGE_EXTS:
            raise UserError(f"not an image file: {target.name}")
        base, mtype = parse_map_name(target.name)
        return {base: [(target, mtype)]}, warnings
    files = sorted(p for p in target.iterdir() if p.is_file())
    image_files = []
    for p in files:
        if p.suffix.lower() in IMAGE_EXTS:
            image_files.append(p)
        else:
            warnings.append(f"warning: skipping non-image file {p.name}")
    if not image_files:
        raise UserError(f"no image files found in {target}")
    return group_inputs(image_files), warnings


def process(
    target: Path,
    out_dir: Path | None,
    score_only: bool = False,
    use_detrend: bool = True,
    band: int | None = None,
    log=print,
) -> int:
    groups, warnings = collect_inputs(target)
    for w in warnings:
        log(w)
    if not score_only and out_dir is None:
        raise UserError("--out <dir> is required unless --score-only is given")
    if not score_only:
        out_dir.mkdir(parents=True, exist_ok=True)

    for base, members in sorted(groups.items()):
        loaded = []
        sizes = set()
        for path, mtype in members:
            arr, alpha, mode = load_image(path)
            loaded.append((path, mtype, arr, alpha, mode))
            sizes.add(arr.shape[:2])
        if len(sizes) > 1:
            detail = ", ".join(
                f"{p.name} is {a.shape[1]}x{a.shape[0]}" for p, _, a, _, _ in loaded
            )
            raise UserError(f"size mismatch in set '{base}': {detail} - all maps in a set must be the same size")
        h, w = loaded[0][2].shape[:2]
        types = {m for _, m, _, _, _ in loaded}
        is_set = len(loaded) > 1 or types != {"single"}
        if is_set and "albedo" not in types and "single" not in types:
            log(f"warning: set '{base}' has no albedo/basecolor map - fixing data maps only")
        if min(h, w) < 8:
            log(f"warning: set '{base}' is only {w}x{h} - too small to blend; scores reported, image copied unchanged")
            geom = None
        else:
            geom = core.blend_geometry(h, w, band)
        if is_set:
            log(
                f"set '{base}': {len(loaded)} map(s), {w}x{h}, "
                f"shared geometry (offset {geom['offset_x']},{geom['offset_y']}, band {geom['band']}px)"
                if geom
                else f"set '{base}': {len(loaded)} map(s), {w}x{h}"
            )
        for path, mtype, arr, alpha, mode in loaded:
            before = core.seam_score(arr)
            if geom is None:
                fixed = arr
                repaint = None
            else:
                repaint = core.band_delta(arr, geom)
                # An unlabelled 16-bit grayscale image is a data map by default.
                detrend = use_detrend and not (mode == "I;16" and mtype == "single")
                fixed = core.fix_array(arr, mtype, geom, use_detrend=detrend)
            after = core.seam_score(fixed)
            tiled = core.tiled_score(fixed)
            extra = f", band repaint {repaint:.1f}" if repaint is not None and repaint > 25.0 else ""
            log(
                f"  {path.name}: seam score {before['score']:.1f} -> {after['score']:.1f} "
                f"(3x3 tiled re-score {tiled:.1f}{extra}) - {core.verdict(after['score'], repaint)}"
            )
            if not score_only:
                save_image(out_dir / path.name, fixed, alpha, mode)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="seamproof",
        description="Make textures tile seamlessly - a whole PBR set at a time, with a measured seam score.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    fix = sub.add_parser("fix", help="fix a texture or a folder of PBR sets")
    fix.add_argument("input", help="image file, or folder of images")
    fix.add_argument("--out", help="output directory (required unless --score-only)")
    fix.add_argument("--score-only", action="store_true", help="measure seam scores, write nothing")
    fix.add_argument("--no-detrend", action="store_true", help="skip the vignette/gradient detrend pass")
    fix.add_argument("--band", type=int, default=None, help="blend band width in pixels (default: 1/8 of the smaller side)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "fix":
            out_dir = Path(args.out) if args.out else None
            if out_dir is not None and not args.score_only:
                out_dir.mkdir(parents=True, exist_ok=True)
            return process(
                Path(args.input),
                out_dir,
                score_only=args.score_only,
                use_detrend=not args.no_detrend,
                band=args.band,
            )
    except UserError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
