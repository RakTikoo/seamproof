"""Tests for seamproof.  All fixtures are synthetic and generated here."""
import io
import struct
import tempfile
import unittest
import zlib
from contextlib import redirect_stderr
from pathlib import Path

import numpy as np
from PIL import Image

from seamproof import cli, core

rng = np.random.default_rng(7)
H = W = 128
YY, XX = np.mgrid[0:H, 0:W]


def vignette_texture() -> np.ndarray:
    base = core.toroidal_gaussian(rng.normal(128, 40, (H, W, 3)), 6.0)
    ramp = (XX[..., None] / W) * 90.0
    bowl = (((XX - W / 2) ** 2 + (YY - H / 2) ** 2) / (W**2) * 60.0)[..., None]
    return np.clip(base + ramp - bowl, 0, 255)


def blob_height() -> np.ndarray:
    hh = np.zeros((H, W))
    r = np.random.default_rng(3)
    for _ in range(14):
        cy, cx = r.integers(0, H), r.integers(0, W)
        amp, rad = r.uniform(60, 160), r.uniform(8, 20)
        hh += amp * np.exp(-((YY - cy) ** 2 + (XX - cx) ** 2) / (2 * rad**2))
    return np.clip(hh, 0, 255)


def save_png(path: Path, arr: np.ndarray) -> None:
    a = np.clip(np.rint(arr), 0, 255).astype(np.uint8)
    if a.ndim == 2:
        Image.fromarray(a, "L").save(path)
    elif a.ndim == 3 and a.shape[2] == 1:
        Image.fromarray(a[:, :, 0], "L").save(path)
    else:
        Image.fromarray(a, "RGB").save(path)


class TestScoreAndFix(unittest.TestCase):
    def setUp(self):
        self.geom = core.blend_geometry(H, W)

    def test_vignette_score_drops_by_large_factor(self):
        img = vignette_texture()
        before = core.seam_score(img)["score"]
        after = core.seam_score(core.fix_array(img, "albedo", self.geom))["score"]
        self.assertGreater(before, 20.0)
        self.assertLess(after, before / 8.0)

    def test_mismatched_halves_score_drops(self):
        img = np.clip(90 + (XX[..., None] >= W // 2) * 90 + rng.normal(0, 18, (H, W, 3)), 0, 255)
        before = core.seam_score(img)["score"]
        after = core.seam_score(core.fix_array(img, "single", self.geom))["score"]
        self.assertGreater(before, 20.0)
        self.assertLess(after, before / 8.0)

    def test_tiled_rescore_agrees_with_score(self):
        fixed = core.fix_array(vignette_texture(), "albedo", self.geom)
        self.assertAlmostEqual(core.seam_score(fixed)["score"], core.tiled_score(fixed), delta=1.0)

    def test_interior_bit_identical_without_detrend(self):
        tex = np.clip(core.toroidal_gaussian(rng.normal(128, 50, (H, W, 3)), 3.0), 0, 255)
        fixed = core.fix_array(tex, "albedo", self.geom, use_detrend=False)
        b = self.geom["band"]
        interior = np.s_[b + 1 : H - b - 1, b + 1 : W - b - 1]
        self.assertTrue(np.array_equal(fixed[interior], tex[interior]))

    def test_normal_map_unit_length_after_fix(self):
        nrm = core.normal_from_height(blob_height()[:, :, None], strength=2.0)
        fixed = core.fix_array(nrm, "normal", self.geom)
        v = fixed / 127.5 - 1.0
        norms = np.linalg.norm(v, axis=2)
        self.assertTrue(np.allclose(norms, 1.0, atol=0.01))

    def test_honesty_unique_object_crossing_edge(self):
        # Giant white disk on the left edge, giant black disk on the right,
        # white bar over the top, black bar under the bottom: no blend can
        # honestly fix this, and the report must not claim success.
        img = np.full((H, W, 3), 105.0) + rng.normal(0, 3, (H, W, 3))
        img[(YY - 64) ** 2 + XX**2 < 42**2] = 252.0
        img[(YY - 64) ** 2 + (XX - (W - 1)) ** 2 < 42**2] = 5.0
        img[:10, :] = 240.0
        img[-10:, :] = 20.0
        before = core.seam_score(img)["score"]
        fixed = core.fix_array(img, "single", self.geom)
        after = core.seam_score(fixed)["score"]
        repaint = core.band_delta(img, self.geom)
        self.assertGreater(before, 100.0)
        self.assertGreater(repaint, 25.0)
        self.assertTrue(core.verdict(after, repaint).startswith("poor"))


class TestRegistration(unittest.TestCase):
    def test_set_stays_registered_after_shared_fix(self):
        height = blob_height()
        normal = core.normal_from_height(height[:, :, None], strength=2.0)
        rough = np.clip(255.0 - height, 0, 255)[:, :, None]
        geom = core.blend_geometry(H, W)
        fixed_h = core.fix_array(height[:, :, None], "height", geom)
        fixed_n = core.fix_array(normal, "normal", geom)
        fixed_r = core.fix_array(rough, "roughness", geom)

        # Same geometry => the set of changed pixels is identical for the
        # two data maps that get no extra per-map step.
        changed_h = np.abs(fixed_h[:, :, 0] - height) > 0
        changed_r = np.abs(fixed_r[:, :, 0] - (255.0 - height)) > 0
        self.assertTrue(np.array_equal(changed_h, changed_r))

        # Recompute the normal from the fixed height; it must still agree
        # with the fixed normal map.
        recomputed = core.normal_from_height(fixed_h, strength=2.0)

        def unit(rgb):
            v = rgb / 127.5 - 1.0
            return v / np.linalg.norm(v, axis=2, keepdims=True)

        cosang = np.clip(np.sum(unit(recomputed) * unit(fixed_n), axis=2), -1, 1)
        ang = np.degrees(np.arccos(cosang))
        self.assertLess(float(ang.mean()), 3.0)
        self.assertLess(float(np.percentile(ang, 95)), 10.0)


class TestNames(unittest.TestCase):
    def test_parse_map_name(self):
        self.assertEqual(cli.parse_map_name("rock_albedo.png"), ("rock", "albedo"))
        self.assertEqual(cli.parse_map_name("rock_basecolor.png"), ("rock", "albedo"))
        self.assertEqual(cli.parse_map_name("rock_normal.png"), ("rock", "normal"))
        self.assertEqual(cli.parse_map_name("rock_roughness.png"), ("rock", "roughness"))
        self.assertEqual(cli.parse_map_name("rock_height.png"), ("rock", "height"))
        self.assertEqual(cli.parse_map_name("photo.png"), ("photo", "single"))


class TestCli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _write_set(self, folder: Path, size=(128, 128), skip=()):
        folder.mkdir(parents=True, exist_ok=True)
        height = blob_height()
        if size != (128, 128):
            height = np.asarray(Image.fromarray(height.astype(np.uint8)).resize(size))
        if "albedo" not in skip:
            save_png(folder / "rock_albedo.png", np.stack([height] * 3, axis=2))
        if "normal" not in skip:
            save_png(folder / "rock_normal.png", core.normal_from_height(height[:, :, None], 2.0))
        if "height" not in skip:
            save_png(folder / "rock_height.png", height)

    def test_fix_folder_set(self):
        src = self.root / "in"
        self._write_set(src)
        out = self.root / "out"
        logs: list[str] = []
        rc = cli.process(src, out, log=logs.append)
        self.assertEqual(rc, 0)
        self.assertEqual(sorted(p.name for p in out.iterdir()),
                         ["rock_albedo.png", "rock_height.png", "rock_normal.png"])
        self.assertTrue(any("shared geometry" in line for line in logs))

    def test_score_only_writes_nothing(self):
        src = self.root / "in"
        self._write_set(src)
        out = self.root / "out"
        rc = cli.main([ "fix", str(src), "--out", str(out), "--score-only"])
        self.assertEqual(rc, 0)
        self.assertFalse(out.exists() and any(out.iterdir()))

    def test_mismatched_sizes_clean_error(self):
        src = self.root / "in"
        self._write_set(src)
        save_png(src / "rock_roughness.png", np.zeros((64, 64)))  # wrong size
        with self.assertRaises(cli.UserError) as ctx:
            cli.process(src, self.root / "out", log=lambda *_: None)
        self.assertIn("size mismatch", str(ctx.exception))

    def test_missing_albedo_warns_but_succeeds(self):
        src = self.root / "in"
        self._write_set(src, skip=("albedo",))
        logs: list[str] = []
        rc = cli.process(src, self.root / "out", log=logs.append)
        self.assertEqual(rc, 0)
        self.assertTrue(any("no albedo" in line for line in logs))

    def test_one_by_one_image(self):
        src = self.root / "tiny.png"
        Image.new("RGB", (1, 1), (200, 10, 10)).save(src)
        out = self.root / "out"
        out.mkdir()
        logs: list[str] = []
        rc = cli.process(src, out, log=logs.append)
        self.assertEqual(rc, 0)
        self.assertTrue(any("too small" in line for line in logs))
        self.assertTrue((out / "tiny.png").exists())

    def test_16_bit_grayscale_png_keeps_interior_precision(self):
        src = self.root / "height16.png"
        out = self.root / "out"
        values = np.linspace(0x1234, 0xFF00, 64 * 64, dtype=np.uint16).reshape(64, 64)
        Image.fromarray(values).save(src)

        self.assertEqual(cli.main(["fix", str(src), "--out", str(out), "--band", "4"]), 0)
        with Image.open(out / src.name) as image:
            self.assertGreaterEqual(np.asarray(image).dtype.itemsize, 2)
            np.testing.assert_array_equal(np.asarray(image)[8:-8, 8:-8], values[8:-8, 8:-8])

    def test_16_bit_rgb_png_is_rejected_without_output(self):
        src = self.root / "rgb16.png"
        out = self.root / "out"

        def chunk(kind, data):
            return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

        header = struct.pack(">IIBBBBB", 1, 1, 16, 2, 0, 0, 0)
        pixels = zlib.compress(struct.pack(">BHHH", 0, 0x1234, 0x4001, 0xFF00))
        src.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", pixels) + chunk(b"IEND", b""))

        err = io.StringIO()
        with redirect_stderr(err):
            rc = cli.main(["fix", str(src), "--out", str(out)])
        self.assertEqual(rc, 2)
        self.assertIn("unsupported 16-bit PNG", err.getvalue())
        self.assertFalse((out / src.name).exists())

    def test_non_image_file_clean_error(self):
        src = self.root / "notes.txt"
        src.write_text("hello")
        err = io.StringIO()
        with redirect_stderr(err):
            rc = cli.main(["fix", str(src), "--out", str(self.root / "out")])
        self.assertEqual(rc, 2)
        self.assertIn("error:", err.getvalue())
        self.assertNotIn("Traceback", err.getvalue())

    def test_folder_with_stray_file_warns_and_continues(self):
        src = self.root / "in"
        self._write_set(src)
        (src / "readme.txt").write_text("hi")
        logs: list[str] = []
        rc = cli.process(src, self.root / "out", log=logs.append)
        self.assertEqual(rc, 0)
        self.assertTrue(any("skipping non-image" in line for line in logs))


if __name__ == "__main__":
    unittest.main()
