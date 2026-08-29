"""
tests/evaluation/elo.py
Advanced Glicko-2 rating system for pairwise evaluation outcomes.

Implements the full Glicko-2 algorithm (Glickman, 2013) with:
  - Rating (μ): equivalent to Elo, starts at 1500
  - Rating Deviation (φ): uncertainty — shrinks as more matches are played
  - Volatility (σ): how erratic the player's performance is
  - 95% confidence intervals for paper-ready reporting
  - Bootstrap significance testing (is A significantly better than B?)

References:
  - Glickman, M.E. (2013). "Example of the Glicko-2 system."
    http://www.glicko.net/glicko/glicko2.pdf
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Literal

# ══════════════════════════════════════════════════════════════════════════════
# Glicko-2 Rating
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class Glicko2Rating:
    """A player's rating in one dimension.

    Attributes
    ----------
    mu : float
        Rating on the Glicko scale (centered at 1500, ~same as Elo).
    phi : float
        Rating deviation — uncertainty. Starts high (350), shrinks with
        more matches. A φ of 50 means we're quite confident; 300 means
        very uncertain.
    sigma : float
        Volatility — how erratic the player's performance is. Low sigma
        means consistent performance.
    """
    mu: float = 1500.0
    phi: float = 350.0
    sigma: float = 0.06

    @property
    def confidence_interval_95(self) -> tuple[float, float]:
        """95% confidence interval: μ ± 1.96 × φ."""
        margin = 1.96 * self.phi
        return (round(self.mu - margin, 1), round(self.mu + margin, 1))

    @property
    def conservative_rating(self) -> float:
        """Lower bound of the 95% CI — pessimistic estimate of true strength.

        Often used for rankings: ranks players by the lower bound so a player
        with fewer matches (higher uncertainty) isn't ranked above a consistent
        player with a similar rating.
        """
        return round(self.mu - 1.96 * self.phi, 1)

    def to_dict(self) -> dict:
        ci = self.confidence_interval_95
        return {
            "rating": round(self.mu, 1),
            "deviation": round(self.phi, 1),
            "volatility": round(self.sigma, 4),
            "ci_95_lower": ci[0],
            "ci_95_upper": ci[1],
            "conservative": self.conservative_rating,
        }


# ══════════════════════════════════════════════════════════════════════════════
# Glicko-2 Tracker
# ══════════════════════════════════════════════════════════════════════════════

# Glicko-2 internal scale factor
_SCALE = 173.7178


class Glicko2Tracker:
    """Advanced rating tracker using the Glicko-2 algorithm.

    Advantages over basic Elo:
    ─────────────────────────
    - Rating Deviation (φ): quantifies uncertainty. More matches → lower φ →
      more confidence. Perfect for paper reporting.
    - Volatility (σ): captures performance consistency. High σ = erratic.
    - Confidence Intervals: each rating comes with a 95% CI.
    - Bootstrap significance: p-values for "is A significantly better than B?"

    Usage:
        tracker = Glicko2Tracker()
        tracker.record_match("llm_output", "ground_truth", "syntax", "A")
        tracker.record_match("llm_output", "ground_truth", "logic", "tie")
        ...
        print(tracker.get_ratings_table())
        sig = tracker.bootstrap_significance("llm_output", "ground_truth", "syntax")
    """

    # System constant τ: controls how much volatility can change per match.
    # Glickman recommends 0.3–1.2; we use 0.5 (moderate).
    TAU = 0.5
    CONVERGENCE_EPSILON = 1e-6

    def __init__(self):
        # player → dimension → Glicko2Rating
        self.ratings: dict[str, dict[str, Glicko2Rating]] = {}
        # Full match log for bootstrap resampling
        self.match_log: list[dict] = []

    def _ensure_player(self, player: str, dimension: str):
        """Create a default rating for a player+dimension if it doesn't exist."""
        if player not in self.ratings:
            self.ratings[player] = {}
        if dimension not in self.ratings[player]:
            self.ratings[player][dimension] = Glicko2Rating()

    # ── Glicko-2 math helpers ─────────────────────────────────

    @staticmethod
    def _g(phi: float) -> float:
        """g(φ) function from Glicko-2 paper."""
        return 1.0 / math.sqrt(1.0 + 3.0 * phi * phi / (math.pi * math.pi))

    @staticmethod
    def _E(mu: float, mu_j: float, phi_j: float) -> float:
        """Expected score E(μ, μ_j, φ_j)."""
        return 1.0 / (1.0 + math.exp(-Glicko2Tracker._g(phi_j) * (mu - mu_j)))

    def _compute_volatility(
        self, sigma: float, phi: float, v: float, delta: float
    ) -> float:
        """Iterative volatility estimation using the Illinois algorithm.

        This is Step 5 of the Glicko-2 algorithm: find the new volatility
        σ' that maximizes the likelihood given the observed performance.
        Uses the Illinois variant of the regula falsi method for guaranteed
        convergence.
        """
        a = math.log(sigma * sigma)
        tau_sq = self.TAU * self.TAU
        phi_sq = phi * phi
        delta_sq = delta * delta

        def f(x: float) -> float:
            ex = math.exp(x)
            num = ex * (delta_sq - phi_sq - v - ex)
            den = 2.0 * (phi_sq + v + ex) ** 2
            return num / den - (x - a) / tau_sq

        # Bracket the root
        A = a
        if delta_sq > phi_sq + v:
            B = math.log(delta_sq - phi_sq - v)
        else:
            k = 1
            while f(a - k * self.TAU) < 0:
                k += 1
            B = a - k * self.TAU

        # Illinois algorithm (modified regula falsi — guaranteed convergence)
        f_A = f(A)
        f_B = f(B)
        while abs(B - A) > self.CONVERGENCE_EPSILON:
            C = A + (A - B) * f_A / (f_B - f_A)
            f_C = f(C)
            if f_C * f_B <= 0:
                A = B
                f_A = f_B
            else:
                f_A /= 2.0
            B = C
            f_B = f_C

        return math.exp(A / 2.0)

    def _update_single(
        self,
        mu: float,
        phi: float,
        sigma: float,
        score: float,
        mu_opp: float,
        phi_opp: float,
    ) -> Glicko2Rating:
        """Update one player's rating given a single match result.

        This implements Steps 3–8 of the Glicko-2 algorithm.

        Parameters
        ----------
        mu, phi, sigma : float
            Current player's rating on the Glicko-2 internal scale.
        score : float
            Actual score (1.0 = win, 0.0 = loss, 0.5 = tie).
        mu_opp, phi_opp : float
            Opponent's rating on the Glicko-2 internal scale.

        Returns
        -------
        Updated Glicko2Rating on the original (Glicko) scale.
        """
        g_opp = self._g(phi_opp)
        e = self._E(mu, mu_opp, phi_opp)

        # Step 3: estimated variance
        v = 1.0 / (g_opp * g_opp * e * (1.0 - e))

        # Step 4: estimated improvement
        delta = v * g_opp * (score - e)

        # Step 5: new volatility
        new_sigma = self._compute_volatility(sigma, phi, v, delta)

        # Step 6: pre-rating period deviation
        phi_star = math.sqrt(phi * phi + new_sigma * new_sigma)

        # Step 7: new phi
        new_phi = 1.0 / math.sqrt(1.0 / (phi_star * phi_star) + 1.0 / v)

        # Step 8: new mu
        new_mu = mu + new_phi * new_phi * g_opp * (score - e)

        # Convert back to Glicko scale
        return Glicko2Rating(
            mu=new_mu * _SCALE + 1500.0,
            phi=new_phi * _SCALE,
            sigma=new_sigma,
        )

    # ── Public API ────────────────────────────────────────────

    def record_match(
        self,
        player_a: str,
        player_b: str,
        dimension: str,
        outcome: Literal["A", "B", "tie"],
    ):
        """Record a pairwise match outcome and update both players' Glicko-2 ratings.

        Parameters
        ----------
        player_a, player_b : str
            Player identifiers (e.g., "llm_output", "ground_truth").
        dimension : str
            Which evaluation dimension this match was on.
        outcome : "A" | "B" | "tie"
            Who won. "A" = player_a wins, "B" = player_b wins.
        """
        self._ensure_player(player_a, dimension)
        self._ensure_player(player_b, dimension)

        # Convert outcome to scores
        if outcome == "A":
            score_a, score_b = 1.0, 0.0
        elif outcome == "B":
            score_a, score_b = 0.0, 1.0
        else:  # tie
            score_a, score_b = 0.5, 0.5

        ra = self.ratings[player_a][dimension]
        rb = self.ratings[player_b][dimension]

        # Convert to Glicko-2 internal scale
        mu_a = (ra.mu - 1500.0) / _SCALE
        phi_a = ra.phi / _SCALE
        mu_b = (rb.mu - 1500.0) / _SCALE
        phi_b = rb.phi / _SCALE

        # Update both players
        new_ra = self._update_single(mu_a, phi_a, ra.sigma, score_a, mu_b, phi_b)
        new_rb = self._update_single(mu_b, phi_b, rb.sigma, score_b, mu_a, phi_a)

        self.ratings[player_a][dimension] = new_ra
        self.ratings[player_b][dimension] = new_rb

        # Log for bootstrap analysis
        self.match_log.append({
            "player_a": player_a,
            "player_b": player_b,
            "dimension": dimension,
            "outcome": outcome,
        })

    def get_rating(self, player: str, dimension: str) -> Glicko2Rating:
        """Get a player's current rating for a dimension."""
        self._ensure_player(player, dimension)
        return self.ratings[player][dimension]

    def get_ratings_table(self) -> list[dict]:
        """Paper-ready table: one row per player × dimension.

        Returns list of dicts with: player, dimension, rating, deviation,
        volatility, ci_95_lower, ci_95_upper, conservative.
        """
        rows = []
        for player in sorted(self.ratings.keys()):
            for dim in sorted(self.ratings[player].keys()):
                r = self.ratings[player][dim]
                row = {"player": player, "dimension": dim}
                row.update(r.to_dict())
                rows.append(row)
        return rows

    def get_win_loss_tie_counts(self) -> dict[str, dict[str, dict[str, int]]]:
        """Compute win/loss/tie counts per player per dimension.

        Returns {player: {dimension: {"wins": N, "losses": N, "ties": N}}}
        """
        counts: dict[str, dict[str, dict[str, int]]] = {}

        for match in self.match_log:
            pa, pb = match["player_a"], match["player_b"]
            dim = match["dimension"]
            outcome = match["outcome"]

            for p in (pa, pb):
                if p not in counts:
                    counts[p] = {}
                if dim not in counts[p]:
                    counts[p][dim] = {"wins": 0, "losses": 0, "ties": 0}

            if outcome == "A":
                counts[pa][dim]["wins"] += 1
                counts[pb][dim]["losses"] += 1
            elif outcome == "B":
                counts[pa][dim]["losses"] += 1
                counts[pb][dim]["wins"] += 1
            else:
                counts[pa][dim]["ties"] += 1
                counts[pb][dim]["ties"] += 1

        return counts

    def get_summary(self) -> dict:
        """Aggregate across dimensions for paper-ready summary.

        Returns {player: {avg_rating, avg_deviation, per_dimension: {...}}}
        """
        summary = {}
        for player in sorted(self.ratings.keys()):
            dims = self.ratings[player]
            all_mu = [r.mu for r in dims.values()]
            all_phi = [r.phi for r in dims.values()]
            summary[player] = {
                "avg_rating": round(sum(all_mu) / len(all_mu), 1) if all_mu else 1500.0,
                "avg_deviation": round(sum(all_phi) / len(all_phi), 1) if all_phi else 350.0,
                "n_dimensions": len(dims),
                "n_matches": sum(
                    1 for m in self.match_log
                    if m["player_a"] == player or m["player_b"] == player
                ),
                "per_dimension": {
                    dim: r.to_dict() for dim, r in sorted(dims.items())
                },
            }
        return summary

    # ── Bootstrap Significance Testing ────────────────────────

    def bootstrap_significance(
        self,
        player_a: str,
        player_b: str,
        dimension: str,
        n_bootstrap: int = 1000,
        seed: int | None = 42,
    ) -> dict:
        """Bootstrap test: is player_a significantly better than player_b?

        Resamples the match log with replacement N times, recomputes
        Glicko-2 ratings each time, and reports:
          - p_value: probability that player_a is NOT better (lower = more significant)
          - mean_delta: average rating difference (A - B)
          - ci_95: 95% confidence interval of the rating difference
          - significant_at_005: True if p < 0.05

        Parameters
        ----------
        player_a, player_b : str
            The two players to compare.
        dimension : str
            Which dimension to test.
        n_bootstrap : int
            Number of bootstrap resamples (default 1000).
        seed : int | None
            Random seed for reproducibility (default 42).
        """
        # Filter relevant matches
        relevant = [
            m for m in self.match_log
            if m["dimension"] == dimension
            and {m["player_a"], m["player_b"]} == {player_a, player_b}
        ]

        if not relevant:
            return {
                "p_value": 1.0,
                "mean_delta": 0.0,
                "ci_95": (0.0, 0.0),
                "significant_at_005": False,
                "n_matches": 0,
                "n_bootstrap": n_bootstrap,
            }

        rng = random.Random(seed)
        deltas = []

        for _ in range(n_bootstrap):
            # Resample with replacement
            sample = rng.choices(relevant, k=len(relevant))

            # Create a fresh tracker and replay the resampled matches
            boot_tracker = Glicko2Tracker()
            for m in sample:
                boot_tracker.record_match(
                    m["player_a"], m["player_b"],
                    dimension, m["outcome"],
                )

            # Get ratings for both players
            ra = boot_tracker.ratings.get(player_a, {}).get(
                dimension, Glicko2Rating()
            )
            rb = boot_tracker.ratings.get(player_b, {}).get(
                dimension, Glicko2Rating()
            )
            deltas.append(ra.mu - rb.mu)

        deltas.sort()
        mean_d = sum(deltas) / len(deltas)
        # p-value: fraction of bootstraps where A was NOT better (delta ≤ 0)
        p_value = sum(1 for d in deltas if d <= 0) / len(deltas)

        # 95% confidence interval
        idx_lo = max(0, int(0.025 * len(deltas)))
        idx_hi = min(len(deltas) - 1, int(0.975 * len(deltas)))
        ci_lo = deltas[idx_lo]
        ci_hi = deltas[idx_hi]

        return {
            "p_value": round(p_value, 4),
            "mean_delta": round(mean_d, 1),
            "ci_95": (round(ci_lo, 1), round(ci_hi, 1)),
            "significant_at_005": p_value < 0.05,
            "n_matches": len(relevant),
            "n_bootstrap": n_bootstrap,
        }

    def bootstrap_all_dimensions(
        self,
        player_a: str,
        player_b: str,
        n_bootstrap: int = 1000,
    ) -> dict[str, dict]:
        """Run bootstrap significance test across all dimensions with matches."""
        all_dims = set()
        for m in self.match_log:
            if {m["player_a"], m["player_b"]} == {player_a, player_b}:
                all_dims.add(m["dimension"])

        return {
            dim: self.bootstrap_significance(player_a, player_b, dim, n_bootstrap)
            for dim in sorted(all_dims)
        }
