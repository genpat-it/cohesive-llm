import unittest

from tests.evaluation.elo import Glicko2Rating, Glicko2Tracker


class TestGlicko2MathUnit(unittest.TestCase):
    def test_glicko2_rating_default_initialization(self):
        rating = Glicko2Rating()
        self.assertEqual(rating.mu, 1500.0)
        self.assertEqual(rating.phi, 350.0)
        self.assertEqual(rating.sigma, 0.06)

        # Test confidence interval and conservative rating
        ci = rating.confidence_interval_95
        self.assertEqual(ci, (814.0, 2186.0))  # 1500 - 1.96 * 350 = 814.0
        self.assertEqual(rating.conservative_rating, 814.0)

    def test_record_match_updates_rating_and_reduces_deviation(self):
        tracker = Glicko2Tracker()

        # Play a match where player_a wins against player_b
        tracker.record_match("player_a", "player_b", "syntax", "A")

        r_a = tracker.get_rating("player_a", "syntax")
        r_b = tracker.get_rating("player_b", "syntax")

        # A should have higher rating than B
        self.assertGreater(r_a.mu, r_b.mu)
        # Deviation (uncertainty) of both should decrease below 350
        self.assertLess(r_a.phi, 350.0)
        self.assertLess(r_b.phi, 350.0)

    def test_record_match_tie_keeps_ratings_similar_but_reduces_deviation(self):
        tracker = Glicko2Tracker()

        tracker.record_match("player_a", "player_b", "syntax", "tie")

        r_a = tracker.get_rating("player_a", "syntax")
        r_b = tracker.get_rating("player_b", "syntax")

        # Both ratings should remain around 1500
        self.assertAlmostEqual(r_a.mu, 1500.0)
        self.assertAlmostEqual(r_b.mu, 1500.0)
        # Both deviations should decrease
        self.assertLess(r_a.phi, 350.0)
        self.assertLess(r_b.phi, 350.0)

    def test_bootstrap_significance_calculation(self):
        tracker = Glicko2Tracker()

        # Record 10 matches where player_a wins all of them against player_b
        for _ in range(10):
            tracker.record_match("player_a", "player_b", "syntax", "A")

        sig = tracker.bootstrap_significance("player_a", "player_b", "syntax", n_bootstrap=100)

        # player_a is significantly better than player_b, so:
        # p_value (probability that A is NOT better than B) should be 0.0 or extremely close to 0
        self.assertLess(sig["p_value"], 0.05)
        self.assertTrue(sig["significant_at_005"])
        self.assertGreater(sig["mean_delta"], 0)

        # Conversely, B vs A should not be significant (p_value should be 1.0 or very high)
        sig_reverse = tracker.bootstrap_significance("player_b", "player_a", "syntax", n_bootstrap=100)
        self.assertGreater(sig_reverse["p_value"], 0.95)
        self.assertFalse(sig_reverse["significant_at_005"])
        self.assertLess(sig_reverse["mean_delta"], 0)
