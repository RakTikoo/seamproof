![seamproof — make a whole PBR texture set tile seamlessly, with a measured seam score](docs/banner.png)

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-17%20passing-brightgreen.svg)](#development)
[![Version](https://img.shields.io/badge/version-0.1.0-blue.svg)](https://github.com/RakTikoo/seamproof/releases)

# seamproof — make a texture or a whole PBR texture set tile seamlessly

Make a texture — or a whole PBR map set — tile seamlessly, with one shared fix across every map and a measured seam score that proves it.

Game engines repeat textures but never repair them. The usual workflow is Photoshop's Offset filter plus hand-healing, or a single-image web fixer that can't handle gradients and vignetting and treats each PBR map separately (so the set drifts out of register). seamproof works on the whole set at once, applies the *same* geometric fix to every map, and certifies the result with a number, not a vibe.

![Ground texture tiled 3x3, before and after](examples/demo_compare.png)

## Install

```console
pip install git+https://github.com/RakTikoo/seamproof
```

Requires Python ≥ 3.10, numpy and Pillow. CPU only; no ML, no network.

## Demo (real output)

```console
$ seamproof fix textures/ --out fixed/
set 'ground': 1 map(s), 256x256, shared geometry (offset 128,128, band 32px)
  ground_albedo.png: seam score 23.7 -> 0.4 (3x3 tiled re-score 0.4) - good - seam not visible in practice
set 'rock': 4 map(s), 256x256, shared geometry (offset 128,128, band 32px)
  rock_albedo.png: seam score 2.9 -> 0.1 (3x3 tiled re-score 0.1) - good - seam not visible in practice
  rock_height.png: seam score 4.7 -> 0.1 (3x3 tiled re-score 0.1) - good - seam not visible in practice
  rock_normal.png: seam score 0.5 -> 0.0 (3x3 tiled re-score 0.0) - good - seam not visible in practice
  rock_roughness.png: seam score 3.1 -> 0.1 (3x3 tiled re-score 0.1) - good - seam not visible in practice
```

Measure without writing anything:

```console
$ seamproof fix textures/ground_albedo.png --score-only
```

## Usage

```console
seamproof fix <file-or-folder> --out <dir> [--score-only] [--no-detrend] [--band N]
```

* A **single file** is fixed on its own.
* A **folder** is scanned for PBR sets. Files that share a base name and differ only by a map suffix form one set:

  | suffixes | map type |
  |---|---|
  | `_albedo`, `_basecolor`, `_base_color`, `_diffuse`, `_color`, `_col` | albedo (colour) |
  | `_normal`, `_nrm`, `_normalgl`, `_normaldx` | normal |
  | `_roughness`, `_rough` | roughness |
  | `_height`, `_displacement`, `_disp`, `_bump` | height |
  | `_metallic`, `_metalness`, `_ao`, `_occlusion` | other data map |

  So `rock_albedo.png`, `rock_normal.png`, `rock_roughness.png` and `rock_height.png` are one set named `rock`. A file with no known suffix is treated as a standalone colour image. All maps in a set must be the same size (a mismatch is a clean error, not a silent resize). A set with no albedo gets a warning and its data maps are still fixed.

## Use in GitHub Actions

This repo is also a composite GitHub Action: it installs seamproof from this repo at the ref you pin, and runs it on a texture file or a folder of textures.

```yaml
- uses: RakTikoo/seamproof@main
  with:
    path: textures/
    out: fixed/
```

Set `score-only: "true"` to measure without writing files. The step fails when seamproof itself errors (exit 2 — for example, maps in one set with different sizes). A completed run exits 0 even when a texture's verdict is *poor*, so read the per-map scores in the step log.

## How it works

One geometry per image size, shared by every map in the set:

1. **Roll** the image by half its size in both axes. The wrap-around edges now meet at a cross through the centre, where there is room to work.
2. **Mirror cross-fade** across that cross: each side is blended with its mirror image using a cosine ramp over a band (default: 1/8 of the smaller side, `--band` to override). The two sides of the original seam are mirror neighbours at the cross, so the fade joins exactly the content that has to meet. A cross-fade in a band was chosen over gradient-domain (Poisson) blending because it is local, exactly invertible in the untouched region, and identical for every map — pixels outside the band are returned **bit-identical** (with `--no-detrend`; see below).
3. **Roll back.** The blended cross becomes the new outer edges, and it is continuous by construction.

Plus, for colour maps only:

4. **Toroidal detrend** (on by default, `--no-detrend` to skip): the low-frequency field is estimated with a wrap-around (FFT) Gaussian and subtracted, adding back its mean. This removes vignette and global gradients — the thing that makes AI-generated and photographed textures show an obvious tiling grid even when their edges technically meet. Data maps (normal / roughness / height) are *never* detrended: changing their low frequencies per-map would pull a set out of register, and their edge mismatch is already handled by the shared blend.

Normal maps are re-normalised to unit length after blending, with z kept positive, so they stay valid tangent-space maps (OpenGL Y+ convention).

## The seam score

The score is a measurement, defined exactly so you can reproduce it:

1. Lightly blur the image with one 3×3 wrap-around box pass (suppresses single-pixel noise so the score reflects visible seams, not sensor grain).
2. Let `S = clamp(min(H, W) // 32, 1, 4)`. For each row, average the leftmost `S` columns and the rightmost `S` columns; the horizontal score is the mean absolute difference between those strip means over all rows and channels, in 0–255 intensity units. The vertical score is the same across the top and bottom strips.
3. The **seam score** is the mean of the horizontal and vertical scores. The CLI also reports a **3×3 tiled re-score**: the fixed texture is actually tiled 3×3 and the same strip comparison is measured across the internal tile boundaries.

Reading the score: ≤ 2 *good* (not visible in practice), ≤ 8 *fair* (faint on close inspection), > 8 *poor*.

**Honesty clause.** A blend can always force edge strips to agree by repainting the seam band with invented content. So the CLI also measures **band repaint**: how much the blend alone changed the seam band. If a low score was bought with a band repaint above 25, the verdict is downgraded to *poor* and says so. A texture with a huge unique object crossing its edge gets a bad report from seamproof, not a fake pass — try the test-suite case (white disk on one edge, black disk on the other): score 158.4 → 10.2, band repaint 51.7, verdict *poor*.

## Honest limits

* **Seamless ≠ repetition-free.** seamproof removes the seam and the vignette grid. If the texture itself contains one memorable feature, tiling it will still show a repeating pattern. No edge fix can change that; use larger textures or detail-map tricks in your engine for that problem.
* Strong, unique content crossing an edge (a face, a trunk, a logo) cannot be hidden by any local blend. seamproof will soften it and report a mediocre score plus a high band repaint instead of pretending otherwise.
* The mirror cross-fade can produce faint symmetric "ghost" shapes inside the blend band on busy textures. The band repaint number is your warning light.
* Detrend flattens *all* low frequency in a colour map, including deliberate broad shading. Use `--no-detrend` if your albedo's gradient is intentional (you will keep more of the seam, and the score will show it).
* One size per set. Supports 8-bit PNG/JPEG/TGA/BMP/WebP/TIFF and 16-bit grayscale PNG without transparency. An unlabelled 16-bit grayscale PNG is treated as data and skips detrending. Unsupported high-bit-depth images produce an error. Supported alpha channels are passed through unchanged (alpha seams are not fixed). Normal maps are assumed tangent-space OpenGL (Y+); a DirectX (Y−) map is blended the same way but its green channel is not flipped for you.
* Images smaller than 8×8 are reported and copied unchanged — there is no room to blend.

## Use with an AI agent

This repo ships a [SKILL.md](SKILL.md) written to the Agent Skills open standard. If your coding agent supports skills, point it at this repo and it can install seamproof, run it on your textures, and read the seam scores and verdicts for you. The skill works when the repo is cloned under its real name (`seamproof`) — a skill's name must match its folder.

## Development

```console
python3 run_tests.py                  # stdlib unittest
python3 examples/generate_examples.py # regenerate the sample textures + demo image
```

The tests include a registration proof: a synthetic set whose normal map is derived from its height map is fixed as a set, the normal is recomputed from the fixed height, and the two are compared (mean angular error < 3°).

## License

MIT
