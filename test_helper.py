"""Run with: python -m unittest -v test_helper.py

These tests exercise logic and image matching, not Windows desktop APIs.
"""
import itertools
import random
import time
import unittest
from unittest.mock import patch

import cv2
import numpy as np
import minesweeper_helper as h
import solve_board


def brute_force(board):
    rows, cols = board.shape
    flat = board.ravel()
    unknown = [i for i, value in enumerate(flat) if value in (h.COVERED, h.FLAG)]
    possible = []
    for bits in itertools.product((0, 1), repeat=len(unknown)):
        mines = {i for i, bit in zip(unknown, bits) if bit}
        if all(value < 0 or sum(j in mines for j in h.neighbours(i, rows, cols)) == value
               for i, value in enumerate(flat)):
            possible.append(mines)
    if not possible:
        return None
    certain = set.intersection(*possible)
    safe = set(unknown) - set.union(*possible)
    return certain, safe


def synthetic_tile(label, variant=0):
    """RGB fixture deliberately includes a covered bevel vs a flat open zero."""
    tile = np.full((32, 32, 3), 195 + variant * 8, dtype=np.uint8)
    if label == h.COVERED:
        tile[:3] = 255
        tile[:, :3] = 255
        tile[-3:] = 90
        tile[:, -3:] = 90
    elif label == h.FLAG:
        cv2.line(tile, (16, 6), (16, 25), (10, 10, 10), 2)
        cv2.fillPoly(tile, [np.array([[16, 6], [8, 11], [16, 15]])], (245, 0, 0))
        cv2.line(tile, (11, 25), (22, 25), (10, 10, 10), 2)
    elif 1 <= label <= 8:
        cv2.putText(tile, str(label), (6, 25), cv2.FONT_HERSHEY_SIMPLEX, .8,
                    (10, 10, 150), 2, cv2.LINE_AA)
    elif label == h.GAME_OVER:
        cv2.circle(tile, (16, 16), 7, (0, 0, 0), -1)
    return tile


class SolverTests(unittest.TestCase):
    def test_neighbours_edges_do_not_wrap(self):
        self.assertEqual(set(h.neighbours(0, 3, 3)), {1, 3, 4})
        self.assertEqual(set(h.neighbours(4, 3, 3)), {0, 1, 2, 3, 5, 6, 7, 8})
        self.assertEqual(set(h.neighbours(2, 3, 3)), {1, 4, 5})

    def test_no_visible_clues_no_hints(self):
        result = solve_board.solve_board(np.full((9, 9), h.COVERED))
        self.assertFalse(result.error)
        self.assertEqual(result.mines, set())
        self.assertEqual(result.safe, set())

    def test_single_mine(self):
        result = solve_board.solve_board(np.array([[1, h.COVERED]]))
        self.assertEqual(result.mines, {1})
        self.assertFalse(result.error)

    def test_single_safe(self):
        result = solve_board.solve_board(np.array([[0, h.COVERED]]))
        self.assertEqual(result.safe, {1})

    def test_ambiguous_no_guess(self):
        result = solve_board.solve_board(np.array([[h.COVERED, 1, h.COVERED]]))
        self.assertEqual(result.mines, set())
        self.assertEqual(result.safe, set())
        self.assertFalse(result.error)

    def test_one_two_one_pattern(self):
        board = np.array([[1, 2, 1], [h.COVERED] * 3])
        result = solve_board.solve_board(board)
        self.assertEqual(result.mines, {3, 5})
        self.assertEqual(result.safe, {4})

    def test_wrong_flag_is_not_assumed_to_be_a_mine(self):
        result = solve_board.solve_board(np.array([[0, h.FLAG]]))
        self.assertFalse(result.error)
        self.assertEqual(result.safe, {1})
        self.assertEqual(result.mines, set())

    def test_flag_remains_part_of_the_constraint(self):
        result = solve_board.solve_board(np.array([[h.COVERED, 1, h.FLAG]]))
        self.assertFalse(result.error)
        self.assertEqual(result.mines, set())
        self.assertEqual(result.safe, set())

    def test_impossible_clue_discards_hints(self):
        result = solve_board.solve_board(np.array([[2, h.COVERED]]))
        self.assertTrue(result.error)
        self.assertFalse(result.mines or result.safe)

    def test_conflicting_clues_discards_hints(self):
        result = solve_board.solve_board(np.array([[0, h.COVERED, 1]]))
        self.assertTrue(result.error)
        self.assertFalse(result.mines or result.safe)

    def test_unknown_tile_halts_entire_board(self):
        result = solve_board.solve_board(np.array([[1, h.COVERED, h.UNREADABLE]]))
        self.assertTrue(result.error)
        self.assertFalse(result.mines or result.safe)

    def test_revealed_mine_halts_entire_board(self):
        result = solve_board.solve_board(np.array([[1, h.COVERED, h.GAME_OVER]]))
        self.assertTrue(result.error)
        self.assertFalse(result.mines or result.safe)

    def test_bad_inputs(self):
        for board in (np.array([]), np.array([1, 2]), np.array([[9]]), np.array([[1.5]])):
            self.assertTrue(solve_board.solve_board(board).error)

    def test_exact_search_forces_cells(self):
        constraints = [(frozenset({0, 1}), 1), (frozenset({1, 2}), 1),
                       (frozenset({0, 2, 3}), 1)]
        mines, safe = h._enumerate_component({0, 1, 2, 3}, constraints,
                                            time.monotonic() + 1)
        self.assertEqual(mines, {1, 3})
        self.assertEqual(safe, {0, 2})

    def test_incomplete_enumeration_raises_instead_of_using_partial_results(self):
        constraints = [(frozenset(range(8)), 4)]
        with self.assertRaises(h.SearchLimit):
            h._enumerate_component(set(range(8)), constraints,
                                   time.monotonic() + 1, max_nodes=20)

    def test_expired_search_never_returns_false_certainty(self):
        result = solve_board.solve_board(np.array([[h.COVERED, 1, h.COVERED]]), budget_seconds=0)
        self.assertTrue(result.limited)
        self.assertFalse(result.mines or result.safe)

    def test_component_size_limit_never_means_guess(self):
        result = solve_board.solve_board(np.array([[h.COVERED, 1, h.COVERED]]), max_component=1)
        self.assertTrue(result.limited)
        self.assertFalse(result.mines or result.safe)

    def test_search_limit_propagates_as_limited_without_hints(self):
        with patch.object(solve_board, "_enumerate_component", side_effect=h.SearchLimit):
            result = solve_board.solve_board(np.array([[h.COVERED, 1, h.COVERED]]))
        self.assertTrue(result.limited)
        self.assertFalse(result.mines or result.safe)

    def test_random_3_by_3_boards_match_exhaustive_reference(self):
        rng = random.Random(1729)
        for sample in range(220):
            actual = {i for i in range(9) if rng.random() < .3}
            board = np.full((3, 3), h.COVERED, dtype=np.int16)
            for i in range(9):
                if i not in actual and rng.random() < .65:
                    board.flat[i] = sum(j in actual for j in h.neighbours(i, 3, 3))
                elif rng.random() < .2:
                    board.flat[i] = h.FLAG  # May deliberately be a WRONG flag.
            expected = brute_force(board)
            self.assertIsNotNone(expected)
            result = solve_board.solve_board(board, budget_seconds=1)
            with self.subTest(sample=sample, board=board.tolist()):
                self.assertFalse(result.error)
                self.assertEqual((result.mines, result.safe), expected)

    def test_random_corrupted_boards_no_unjustified_hints(self):
        rng = random.Random(42)
        for sample in range(80):
            board = np.full((3, 3), h.COVERED, dtype=np.int16)
            for i in rng.sample(range(9), rng.randrange(1, 6)):
                board.flat[i] = rng.randrange(4)
            expected = brute_force(board)
            result = solve_board.solve_board(board, budget_seconds=1)
            if expected is None:
                self.assertTrue(result.error, board.tolist())
            else:
                self.assertFalse(result.error)
                self.assertEqual((result.mines, result.safe), expected)

    def test_deductions_are_recomputed_not_cached_between_boards(self):
        before = solve_board.solve_board(np.array([[1, h.COVERED]]))
        after = solve_board.solve_board(np.array([[0, h.COVERED]]))
        self.assertEqual(before.mines, {1})
        self.assertEqual(after.mines, set())
        self.assertEqual(after.safe, {1})


class VisionTests(unittest.TestCase):
    def test_untrained_means_unreadable(self):
        bank = h.TemplateBank()
        values, _ = bank.classify([synthetic_tile(h.COVERED)])
        self.assertEqual(values.tolist(), [h.UNREADABLE])

    def test_every_exact_known_tile_is_recognised(self):
        bank = h.TemplateBank()
        for label in h.LABELS:
            bank.add(label, synthetic_tile(label))
        values, _ = bank.classify([synthetic_tile(label) for label in h.LABELS])
        self.assertEqual(values.tolist(), h.LABELS)

    def test_unseen_digit_is_not_silently_a_blank(self):
        bank = h.TemplateBank()
        for label in (h.COVERED, 0, 1, 2, 3):
            bank.add(label, synthetic_tile(label))
        values, _ = bank.classify([synthetic_tile(8)])
        self.assertEqual(values.tolist(), [h.UNREADABLE])

    def test_same_image_cannot_receive_conflicting_labels(self):
        bank = h.TemplateBank()
        bank.add(0, synthetic_tile(0))
        with self.assertRaises(ValueError):
            bank.add(h.COVERED, synthetic_tile(0))

    def test_duplicate_same_label_is_not_added_twice(self):
        bank = h.TemplateBank()
        bank.add(0, synthetic_tile(0))
        bank.add(0, synthetic_tile(0))
        self.assertEqual(len(bank.samples), 1)

    def test_colour_variants_supported(self):
        bank = h.TemplateBank()
        bank.add(0, synthetic_tile(0))
        bank.add(0, synthetic_tile(0, 1))
        values, _ = bank.classify([synthetic_tile(0, 1)])
        self.assertEqual(values.tolist(), [0])

    def test_profile_round_trip(self):
        bank = h.TemplateBank()
        bank.add(3, synthetic_tile(3))
        restored = h.TemplateBank.from_json(bank.to_json())
        self.assertEqual(restored.samples[0][0], 3)
        np.testing.assert_array_equal(restored.samples[0][1], bank.samples[0][1])

    def test_profile_bad_data_rejected(self):
        for records in ([{"label": 99, "png": ""}], [{"label": 1, "png": "***"}], {}):
            with self.assertRaises((ValueError, KeyError)):
                h.TemplateBank.from_json(records)

    def test_clone_is_independent(self):
        bank = h.TemplateBank()
        bank.add(1, synthetic_tile(1))
        clone = bank.clone()
        clone.samples[0][1][0, 0] = 0
        self.assertFalse(np.array_equal(bank.samples[0][1], clone.samples[0][1]))

    def test_even_grid_splits_exactly(self):
        frame = np.concatenate([synthetic_tile(1), synthetic_tile(2)], axis=1)
        tiles = h.split_tiles(frame, 1, 2)
        np.testing.assert_array_equal(tiles[0], synthetic_tile(1))
        np.testing.assert_array_equal(tiles[1], synthetic_tile(2))

    def test_fractional_grid_covers_the_frame(self):
        frame = np.zeros((101, 103, 3), dtype=np.uint8)
        tiles = h.split_tiles(frame, 3, 3)
        self.assertEqual(len(tiles), 9)
        self.assertEqual(sum(t.shape[0] * t.shape[1] for t in tiles), 101 * 103)

    def test_small_crop_rejected(self):
        with self.assertRaises(ValueError):
            h.split_tiles(np.zeros((15, 15, 3), np.uint8), 9, 9)

    def test_synthetic_screen_to_mine_inference(self):
        labels = [[1, 2, 1], [h.COVERED, h.COVERED, h.COVERED]]
        frame = np.concatenate([np.concatenate([synthetic_tile(x) for x in row], axis=1)
                                for row in labels], axis=0)
        bank = h.TemplateBank()
        for label in (1, 2, h.COVERED):
            bank.add(label, synthetic_tile(label))
        board, result = h.analyse_frame(frame, 2, 3, bank, h.DEFAULT_THRESHOLD)
        self.assertEqual(board.tolist(), labels)
        self.assertEqual(result.mines, {3, 5})
        self.assertEqual(result.safe, {4})

    def test_synthetic_screen_flag_update(self):
        bank = h.TemplateBank()
        for label in (1, 2, h.COVERED, h.FLAG):
            bank.add(label, synthetic_tile(label))
        labels = [[1, 2, 1], [h.FLAG, h.COVERED, h.COVERED]]
        frame = np.concatenate([np.concatenate([synthetic_tile(x) for x in row], axis=1)
                                for row in labels], axis=0)
        board, result = h.analyse_frame(frame, 2, 3, bank, h.DEFAULT_THRESHOLD)
        self.assertEqual(result.mines, {3, 5})
        to_overlay = {i for i in result.mines if board.flat[i] == h.COVERED}
        self.assertEqual(to_overlay, {5})


if __name__ == "__main__":
    unittest.main(verbosity=2)
