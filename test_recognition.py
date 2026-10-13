"""v2 image tests. Run: python -m unittest -v test_helper.py test_recognition.py

The screenshot fixture was supplied by the user. Tests based on its resampling
are robustness checks on this ONE scene, not independent game/theme validation.
Actual 6/7/8s are absent from it; those glyphs are tested with synthetic fixtures.
"""
from pathlib import Path
import unittest

import cv2
import numpy as np
import minesweeper_helper as h
from test_helper import synthetic_tile

# Visible labels checked against the supplied 16-row x 30-column screenshot.
EXPECTED_TEXT = """
0 2 F 2 0 1 1 2 F 1 0 0 0 1 1 2 F 1 1 1 1 1 F 1 1 1 2 C C 1
1 3 F 2 0 1 F 2 1 2 1 1 1 3 F 3 1 1 1 F 1 1 1 1 2 F 4 C C C
F 2 1 1 0 1 1 1 0 1 F 1 2 F F 4 1 0 1 1 1 0 1 1 3 F C C C C
2 3 2 2 1 1 0 0 0 1 1 1 3 F F F 3 1 0 0 0 0 1 F 2 C C C C C
2 F F 2 F 1 0 0 0 0 0 0 2 F 5 F F 2 1 1 0 0 1 1 2 C C C C C
2 F 5 4 2 1 0 0 0 1 1 2 2 2 2 2 2 3 F 2 0 0 1 2 4 F C C C C
1 2 F F 1 0 0 0 0 1 F 3 F 2 0 0 0 2 F 2 0 0 1 F F F C C C C
0 1 3 4 3 1 0 0 1 2 2 3 F 3 1 2 2 3 2 1 0 0 1 4 F C C C C C
1 1 2 F F 1 0 0 1 F 1 1 1 3 F 3 F F 2 1 1 0 0 3 F C C C C C
2 F 3 3 3 2 1 2 2 2 1 0 1 3 F 3 2 3 4 F 2 0 0 2 F C C C C C
F 3 2 F 1 2 F 3 F 2 1 1 2 F 3 2 1 2 F F 3 2 2 3 3 C C C C C
F 3 2 2 1 2 F 3 2 C C C C C C C C C C 3 C C F C C C C C C C
2 3 F 1 0 2 2 3 2 C C C C C C C C C C C C C C C C C C C C C
1 F 2 2 1 2 F 3 F C C C C C C C C C C C C C C C C C C 2 C C
1 1 1 2 F 3 2 C C C C C C C C C C C C C C C C C C C C 2 1 1
0 0 0 2 F C C C C C C C C C C C C C C C C C C C C C C 1 0 0
"""
TOKEN = {**{str(i): i for i in range(9)}, "C": h.COVERED, "F": h.FLAG}
EXPECTED = np.array([[TOKEN[t] for t in row.split()]
                     for row in EXPECTED_TEXT.strip().splitlines()], dtype=np.int16)


def classic_synthetic(label, font=cv2.FONT_HERSHEY_SIMPLEX):
    im = np.full((32, 32, 3), 198, np.uint8)
    im[:2] = 128
    im[:, :2] = 128
    if label in (h.COVERED, h.FLAG):
        im[:4] = 255
        im[:, :4] = 255
        im[-4:] = 128
        im[:, -4:] = 128
    if 1 <= label <= 8:
        colour = tuple(int(c) for c in h.CLASSIC_PALETTE[label - 1]) if label <= 6 else (
            (0, 0, 0) if label == 7 else (128, 128, 128))
        cv2.putText(im, str(label), (5, 26), font, .85, colour, 2, cv2.LINE_AA)
    elif label == h.FLAG:
        cv2.line(im, (17, 8), (17, 24), (0, 0, 0), 2)
        cv2.line(im, (12, 25), (22, 25), (0, 0, 0), 3)
        cv2.fillPoly(im, [np.array([[17, 7], [9, 10], [17, 14]])], (240, 0, 0))
    return im


class ClassicScreenshotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(__file__).parent / "test_data" / "classic_screenshot.png"
        image = cv2.imread(str(path))
        if image is None:
            raise unittest.SkipTest("Screenshot fixture not found; extract the full v2 ZIP for image tests.")
        cls.image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        cls.frame = cls.image[106:554, 30:870]

    def check_frame(self, frame, allow_unknown=False):
        bank = h.TemplateBank(classic_auto=True)
        values, _ = bank.classify(h.split_tiles(frame, 16, 30))
        board = values.reshape(16, 30)
        self.assertEqual(bank.samples, [])  # No examples or auto-training involved.
        if allow_unknown:
            mask = board != h.UNREADABLE
            np.testing.assert_array_equal(board[mask], EXPECTED[mask])
        else:
            np.testing.assert_array_equal(board, EXPECTED)
        return board

    def test_all_480_cells_without_teaching(self):
        self.check_frame(self.frame)

    def test_all_25_combinations_of_two_pixel_selection_offsets(self):
        for dx in range(-2, 3):
            for dy in range(-2, 3):
                with self.subTest(dx=dx, dy=dy):
                    self.check_frame(self.image[106+dy:554+dy, 30+dx:870+dx])

    def test_resized_boards_with_three_resamplers(self):
        for factor in (.5, .75, 1.25, 1.5, 2.):
            for method in (cv2.INTER_NEAREST, cv2.INTER_LINEAR, cv2.INTER_AREA):
                with self.subTest(factor=factor, method=method):
                    self.check_frame(cv2.resize(self.frame, None, fx=factor, fy=factor,
                                               interpolation=method))

    def test_irregular_low_resolution_scale_never_invents_a_label(self):
        # At 65% scale with area resampling one of the two 5s can be unreadable.
        # A withheld hint is preferable to loosening the test until it guesses.
        frame = cv2.resize(self.frame, None, fx=.65, fy=.65, interpolation=cv2.INTER_AREA)
        self.check_frame(frame, allow_unknown=True)

    def test_moderate_brightness_and_contrast_changes(self):
        for scale, offset in ((1, -20), (1, 20), (.85, 10), (1.08, -10)):
            with self.subTest(scale=scale, offset=offset):
                frame = np.clip(self.frame.astype(float) * scale + offset, 0, 255).astype(np.uint8)
                self.check_frame(frame)

    def test_pixel_noise(self):
        rng = np.random.default_rng(42)
        for sigma in (2, 5, 10):
            with self.subTest(sigma=sigma):
                frame = np.clip(self.frame.astype(float) + rng.normal(0, sigma, self.frame.shape),
                                0, 255).astype(np.uint8)
                self.check_frame(frame)

    def test_slight_blur(self):
        for sigma in (.4, .6, .8, 1.):
            with self.subTest(sigma=sigma):
                self.check_frame(cv2.GaussianBlur(self.frame, (3, 3), sigma))

    def test_recognised_board_has_consistent_clues(self):
        board, result = h.analyse_frame(self.frame, 16, 30, h.TemplateBank(classic_auto=True),
                                       h.DEFAULT_THRESHOLD)
        np.testing.assert_array_equal(board, EXPECTED)
        self.assertFalse(result.error)
        self.assertFalse(result.mines & result.safe)

    def test_old_examples_need_only_one_sample_per_visible_type(self):
        tiles = h.split_tiles(self.frame, 16, 30)
        bank = h.TemplateBank()  # Automatic mode OFF: exercise the new matcher.
        for label in np.unique(EXPECTED):
            idx = int(np.flatnonzero(EXPECTED.ravel() == label)[0])
            bank.add(int(label), tiles[idx])
        values, _ = bank.classify(tiles)
        np.testing.assert_array_equal(values.reshape(16, 30), EXPECTED)


class RecognitionSafetyTests(unittest.TestCase):
    def test_all_classic_digits_in_two_synthetic_fonts(self):
        for font in (cv2.FONT_HERSHEY_SIMPLEX, cv2.FONT_HERSHEY_DUPLEX):
            for label in range(1, 9):
                with self.subTest(font=font, label=label):
                    self.assertEqual(h.classify_classic_tile(classic_synthetic(label, font)), label)

    def test_synthetic_covered_empty_and_flag_are_different(self):
        for label in (h.COVERED, 0, h.FLAG):
            with self.subTest(label=label):
                self.assertEqual(h.classify_classic_tile(classic_synthetic(label)), label)

    def test_flat_flag_graphic_is_not_a_red_three(self):
        flag = classic_synthetic(h.FLAG)
        flag[:4] = 198
        flag[:, :4] = 198
        flag[-4:] = 198
        flag[:, -4:] = 198
        self.assertEqual(h.classify_classic_tile(flag), h.UNREADABLE)

    def test_mine_icons_are_not_black_sevens_or_zeros(self):
        for colour in ((0, 0, 0), (128, 128, 128)):
            for spikes in (False, True):
                for glint in (False, True):
                    im = classic_synthetic(0)
                    cv2.circle(im, (16, 16), 7, colour, -1)
                    if spikes:
                        for dx, dy in ((11, 0), (0, 11), (8, 8), (8, -8)):
                            cv2.line(im, (16-dx, 16-dy), (16+dx, 16+dy), colour, 2)
                    if glint:
                        cv2.rectangle(im, (12, 12), (14, 14), (255, 255, 255), -1)
                    with self.subTest(colour=colour, spikes=spikes, glint=glint):
                        self.assertEqual(h.classify_classic_tile(im), h.UNREADABLE)

    def test_question_mark_and_solid_colour_do_not_become_clues(self):
        for char in ("?", "X"):
            im = classic_synthetic(h.COVERED)
            cv2.putText(im, char, (5, 25), cv2.FONT_HERSHEY_SIMPLEX, .8, (0, 0, 0), 2)
            self.assertEqual(h.classify_classic_tile(im), h.UNREADABLE)
        im = classic_synthetic(0)
        cv2.rectangle(im, (8, 6), (22, 25), (236, 0, 0), -1)
        self.assertEqual(h.classify_classic_tile(im), h.UNREADABLE)

    def test_exploded_red_background_does_not_become_three(self):
        im = np.full((32, 32, 3), (245, 0, 0), np.uint8)
        cv2.circle(im, (16, 16), 7, (0, 0, 0), -1)
        self.assertEqual(h.classify_classic_tile(im), h.UNREADABLE)

    def test_unknown_removes_all_solver_hints(self):
        frame = np.concatenate([classic_synthetic(1), classic_synthetic(h.COVERED),
                                np.zeros((32, 32, 3), np.uint8)], axis=1)
        board, result = h.analyse_frame(frame, 1, 3, h.TemplateBank(classic_auto=True),
                                       h.DEFAULT_THRESHOLD)
        self.assertIn(h.UNREADABLE, board)
        self.assertTrue(result.error)
        self.assertFalse(result.mines or result.safe)

    def test_auto_mode_is_preserved_in_clone(self):
        bank = h.TemplateBank(classic_auto=True)
        self.assertTrue(bank.clone().classic_auto)
        self.assertFalse(h.TemplateBank().clone().classic_auto)

    def test_tolerant_custom_matcher_accepts_small_shifts_and_brightness(self):
        bank = h.TemplateBank()
        for label in h.LABELS:
            bank.add(label, synthetic_tile(label))
        for label in h.LABELS:
            for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                tile = cv2.warpAffine(synthetic_tile(label), np.float32([[1, 0, dx], [0, 1, dy]]),
                                      (32, 32), borderMode=cv2.BORDER_REPLICATE)
                tile = np.clip(tile.astype(int) + 4, 0, 255).astype(np.uint8)
                values, _ = bank.classify([tile])
                with self.subTest(label=label, dx=dx, dy=dy):
                    self.assertEqual(int(values[0]), label)

    def test_ambiguous_custom_examples_are_not_forced_to_a_label(self):
        bank = h.TemplateBank()
        bank.add(0, synthetic_tile(0))
        tile = synthetic_tile(0).copy()
        tile[15, 15] = 194
        bank.add(h.COVERED, tile)
        values, _ = bank.classify([synthetic_tile(0)])
        self.assertEqual(int(values[0]), h.UNREADABLE)

    def test_taught_fallback_works_when_classic_cannot_recognise_a_design(self):
        bank = h.TemplateBank(classic_auto=True)
        tile = np.full((32, 32, 3), (25, 50, 30), np.uint8)
        cv2.putText(tile, "2", (6, 25), cv2.FONT_HERSHEY_SIMPLEX, .8, (255, 255, 255), 2)
        self.assertEqual(h.classify_classic_tile(tile), h.UNREADABLE)
        bank.add(2, tile)
        values, _ = bank.classify([tile])
        self.assertEqual(int(values[0]), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
