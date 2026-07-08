"""Macro OS v4.1 — HMM OFFLINE TRAINING ONLY.

STRICT RULE: This module is NEVER imported by the live pipeline.
Only invoked by hmm_train_job.py (offline job).

All writes are atomic: params.tmp -> rename -> params.json
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.schemas import RegimeType

logger = logging.getLogger(__name__)


def compute_emission_params(
    replay_data: List[Dict[str, Any]],
    ground_truth: List[Dict[str, Any]],
) -> Dict[str, Dict[str, Dict[str, float]]]:
    """Compute emission parameters (mu, sigma) per regime per feature.

    Args:
        replay_data: List of replay event dicts with features.
        ground_truth: List of ground truth labels.

    Returns:
        Dict: regime -> feature -> {mu, sigma}
    """
    regimes = [r.value for r in RegimeType]
    feature_collect: Dict[str, Dict[str, List[float]]] = {
        r: {} for r in regimes
    }

    for event, gt in zip(replay_data, ground_truth):
        gt_regime = gt.get("regime", "TRANSITION")
        if gt_regime not in feature_collect:
            continue

        features = event.get("features", event.get("payload", {}))
        for key, val in features.items():
            if key.startswith("_") or not isinstance(val, (int, float)):
                continue
            if key not in feature_collect[gt_regime]:
                feature_collect[gt_regime][key] = []
            feature_collect[gt_regime][key].append(float(val))

    emission: Dict[str, Dict[str, Dict[str, float]]] = {}
    for regime, feat_map in feature_collect.items():
        emission[regime] = {}
        for feat_key, values in feat_map.items():
            if not values:
                continue
            mu = sum(values) / len(values)
            variance = sum((v - mu) ** 2 for v in values) / len(values)
            sigma = variance ** 0.5 if variance > 0 else 1.0
            emission[regime][feat_key] = {
                "mu": round(mu, 4),
                "sigma": round(sigma, 4),
            }

    return emission


def compute_transition_matrix(
    ground_truth: List[Dict[str, Any]],
) -> Dict[str, Dict[str, float]]:
    """Compute state transition counts / probabilities from ground truth.

    Args:
        ground_truth: List of ground truth labels.

    Returns:
        Dict: from_regime -> {to_regime: probability}
    """
    regimes = [r.value for r in RegimeType]
    counts: Dict[str, Dict[str, int]] = {
        r: {t: 0 for t in regimes} for r in regimes
    }

    for i in range(1, len(ground_truth)):
        prev = ground_truth[i - 1].get("regime", "TRANSITION")
        curr = ground_truth[i].get("regime", "TRANSITION")
        if prev in counts and curr in counts[prev]:
            counts[prev][curr] += 1

    transition: Dict[str, Dict[str, float]] = {}
    for from_r, to_counts in counts.items():
        total = sum(to_counts.values())
        if total > 0:
            transition[from_r] = {
                to_r: round(cnt / total, 4) for to_r, cnt in to_counts.items()
            }
        else:
            transition[from_r] = {to_r: 0.25 for to_r in regimes}

    return transition


def train_hmm(
    replay_data: List[Dict[str, Any]],
    ground_truth: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Train HMM parameters from replay data and ground truth.

    Args:
        replay_data: List of replayed event dicts.
        ground_truth: List of ground truth label dicts.

    Returns:
        Dict with transition_matrix and emission_params.
    """
    emission = compute_emission_params(replay_data, ground_truth)
    transition = compute_transition_matrix(ground_truth)

    return {
        "transition_matrix": transition,
        "emission_params": emission,
        "states": [r.value for r in RegimeType],
    }


def save_model_atomic(params: Dict[str, Any], path: Path) -> None:
    """Atomically write model params to file.

    Writes to .tmp then renames to target to prevent partial reads
    by the live inference pipeline.

    Args:
        params: Model parameters dict.
        path: Target file path (e.g. vault/HMM_PARAMS.json).
    """
    tmp_path = path.with_suffix(".json.tmp")
    try:
        with open(tmp_path, "w") as f:
            json.dump(params, f, indent=2)
        os.replace(str(tmp_path), str(path))
        logger.info("Model saved atomically to %s", path)
    except (IOError, OSError) as e:
        logger.error("Failed to save model: %s", e)
        # Clean up temp file on failure
        if tmp_path.exists():
            tmp_path.unlink()
        raise


def load_replay_data(path: Path) -> List[Dict[str, Any]]:
    """Load replay output data from JSONL."""
    events: List[Dict[str, Any]] = []
    if not path.exists():
        return events
    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events


def load_ground_truth(path: Path) -> List[Dict[str, Any]]:
    """Load ground truth labels from JSONL."""
    return load_replay_data(path)
