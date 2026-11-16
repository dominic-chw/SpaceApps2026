"""Learned match weights: exact ranks, no train/test leakage, bounded multipliers
(pipeline/validation/learn_weights.py)."""
import numpy as np
import pytest

from pipeline.validation import evaluate
from pipeline.validation import learn_weights as lw


def random_places(n=300, seed=0):
    rng = np.random.default_rng(seed)
    return rng.uniform(-60, 80, n), rng.uniform(-180, 180, n), rng


def test_fast_rank_equals_spaced_ranks():
    lats, lons, rng = random_places()
    near = lw.spacing_matrix(lats, lons)
    for trial in range(25):
        d = rng.random(len(lats))
        d[rng.random(len(lats)) < 0.05] = np.nan
        target = np.zeros(len(lats), bool)
        target[rng.choice(len(lats), size=rng.integers(1, 6), replace=False)] = True
        ranks, _ = evaluate.spaced_ranks(d, lats, lons)
        assert lw.best_target_rank(d, near, target, lats, lons) == ranks[target].min()


def synthetic_items(n_groups=5, per_group=2, n_features=4, seed=0):
    """Pairs whose true site is closest on feature 0 only, so up-weighting feature 0 helps."""
    lats, lons, rng = random_places(120, seed)
    near = lw.spacing_matrix(lats, lons)
    items = []
    for g in range(n_groups):
        for k in range(per_group):
            diffs = rng.random((len(lats), n_features))
            target = np.zeros(len(lats), bool)
            target[rng.integers(len(lats))] = True
            diffs[target, 0] = 0.0
            items.append({"pair_id": g * 10 + k, "group": f"site{g}", "band": "skin", "diffs": diffs,
                          "present": np.ones_like(diffs, bool), "diffs0": diffs, "base": np.ones(n_features),
                          "feature_index": np.arange(n_features), "min_coverage": 0.5, "is_target": target,
                          "near": near, "lats": lats, "lons": lons})
    return items


def test_multipliers_stay_in_bounds():
    mult = lw.fit(synthetic_items(), 4, n_draws=40)
    assert ((mult >= lw.BOUNDS[0] - 1e-9) & (mult <= lw.BOUNDS[1] + 1e-9)).all()


def test_learning_helps_when_there_is_signal():
    items = synthetic_items()
    mult = lw.fit(items, 4, n_draws=40)
    learned = np.mean([lw.pair_rank(it, mult) for it in items])
    hand = np.mean([lw.pair_rank(it, np.ones(4)) for it in items])
    assert learned <= hand and mult[0] > 1


def test_held_out_pairs_never_in_training(monkeypatch):
    items = synthetic_items()
    seen = []
    real_fit = lw.fit

    def spy(train, n_features, n_draws=lw.N_DRAWS, seed=lw.SEED):
        seen.append({it["group"] for it in train})
        return real_fit(train, n_features, n_draws=5, seed=seed)

    monkeypatch.setattr(lw, "fit", spy)
    rows = lw.leave_one_group_out(items, 4, n_draws=5)
    groups = sorted({it["group"] for it in items})
    assert len(seen) == len(groups)
    for held_out, train_groups in zip(groups, seen):
        assert held_out not in train_groups
        assert train_groups == set(groups) - {held_out}
    assert sorted(r["pair_id"] for r in rows) == sorted(it["pair_id"] for it in items)
