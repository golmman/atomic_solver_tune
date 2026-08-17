"""Parameter mapping between CMA-ES latent vectors and atomic_solver TOML."""

import math
from collections import OrderedDict

# Piece values. These are now part of the tuned parameter set, but they are
# kept in a separate table because the Rust config groups them under
# [scorer.pieces].
PIECES_DEFAULTS = OrderedDict([
    ("pieces.pawn", 100),
    ("pieces.knight", 320),
    ("pieces.bishop", 330),
    ("pieces.rook", 500),
    ("pieces.queen", 900),
    ("pieces.commoner", 20_000),
])

# Sensible first subset: all non-piece ScorerParams fields.
# score_winning_capture and score_promotion are intentionally fixed because they
# are huge, almost-categorical thresholds; tuning them safely requires extra
# hierarchy care that is deferred.
SCORER_DEFAULTS = OrderedDict([
    ("score_capture", 5_000),
    ("capture_net_scale", 10),
    ("score_threat_last", 10_000),
    ("score_threat", 1_000),
    ("score_kamikaze_last", 9_000),
    ("score_kamikaze", 3_000),
    ("score_approach", 100),
    ("score_approach_step", 10),
    ("score_center", 50),
    ("score_center_step", 10),
    ("score_pawn_storm", 5_500),
    ("score_pawn_storm_step", 100),
    ("score_rook_center", 500),
    ("score_rook_open_file", 2_000),
    ("score_rook_open_file_step", 50),
    ("score_rook_back_rank", 300),
    ("and_pawn_storm_scale", 50),
    ("and_rook_attack_scale", 50),
    ("and_approach_scale", 75),
])

# Complete ordered list of parameters that the tuner optimises.
TUNED_DEFAULTS = OrderedDict(list(SCORER_DEFAULTS.items()) + list(PIECES_DEFAULTS.items()))

# Fixed thresholds from the default config.
SCORE_PROMOTION = 1_000_000
SCORE_WINNING_CAPTURE = 100_000_000

# Prevent overflow in the solver's i32 computations and in derived hierarchy checks.
MAX_COMPONENT = 10_000_000
MAX_I32 = 2_147_483_647


def _get_piece_value(raw, name):
    return int(raw.get(name, PIECES_DEFAULTS[name]))


def decode(x):
    """Map a latent CMA-ES vector to a valid ScorerParams dict.

    Returns None if the vector cannot be mapped to a valid config (overflow or
    unsatisfiable hierarchy).
    """
    if len(x) != len(TUNED_DEFAULTS):
        raise ValueError(f"latent vector length {len(x)} != {len(TUNED_DEFAULTS)}")

    raw = {}
    for i, (name, default) in enumerate(TUNED_DEFAULTS.items()):
        xi = x[i]
        v = round(default * math.exp(xi))
        if name.startswith("and_"):
            # Percent scale in [0, 100].
            v = min(100, max(0, v))
        elif name.startswith("pieces."):
            # Piece values must be strictly positive.
            v = min(max(v, 1), MAX_COMPONENT)
        else:
            # Clamp to a safe range to avoid i32 overflow in quiet-move sums.
            v = min(max(v, 0), MAX_COMPONENT)
        raw[name] = v

    # Enforce a sensible piece-value hierarchy:
    #   pawn < knight, bishop
    #   knight/bishop < rook < queen
    #   commoner > sum(pawn..queen)
    # If a latent sample violates this, clamp up the offending values.  This
    # keeps configs valid without rejecting huge regions of the search space.
    p_pawn = _get_piece_value(raw, "pieces.pawn")
    p_knight = max(_get_piece_value(raw, "pieces.knight"), p_pawn + 1)
    p_bishop = max(_get_piece_value(raw, "pieces.bishop"), p_pawn + 1)
    p_rook = max(_get_piece_value(raw, "pieces.rook"), max(p_knight, p_bishop) + 1)
    p_queen = max(_get_piece_value(raw, "pieces.queen"), p_rook + 1)
    other_sum = p_pawn + p_knight + p_bishop + p_rook + p_queen
    p_commoner = max(_get_piece_value(raw, "pieces.commoner"), other_sum + 1)

    raw["pieces.pawn"] = p_pawn
    raw["pieces.knight"] = p_knight
    raw["pieces.bishop"] = p_bishop
    raw["pieces.rook"] = p_rook
    raw["pieces.queen"] = p_queen
    raw["pieces.commoner"] = p_commoner

    # Capture < promotion hierarchy. The maximum non-winning capture removes
    # every non-commoner enemy piece with a pawn, so the net material swing
    # depends on the tuned piece values.
    score_capture = raw["score_capture"]
    capture_net_scale = raw["capture_net_scale"]
    max_non_commoner_value = p_queen + p_rook + p_bishop + p_knight
    max_capture_net = max_non_commoner_value - p_pawn
    max_capture_score = score_capture + capture_net_scale * max_capture_net
    if max_capture_score >= SCORE_PROMOTION:
        factor = (SCORE_PROMOTION - 1) / max_capture_score
        score_capture = int(score_capture * factor)
        capture_net_scale = int(capture_net_scale * factor)
        max_capture_score = score_capture + capture_net_scale * max_capture_net

    if max_capture_score >= SCORE_PROMOTION or max_capture_score > MAX_I32:
        return None

    raw["score_capture"] = score_capture
    raw["capture_net_scale"] = capture_net_scale

    # Add fixed hierarchy fields.
    raw["score_promotion"] = SCORE_PROMOTION
    raw["score_winning_capture"] = SCORE_WINNING_CAPTURE

    # Final overflow safety check.
    for k, v in raw.items():
        if not isinstance(v, int):
            v = int(round(v))
        if v < 0 or v > MAX_I32:
            return None
        raw[k] = v

    return raw


def _lookup_flat(params, name, default):
    """Look up a possibly-nested value from a TOML-style params dict."""
    if name in params:
        return params[name]
    if name.startswith("pieces."):
        pieces = params.get("pieces", {})
        if isinstance(pieces, dict):
            return pieces.get(name.split(".", 1)[1], default)
    return default


def encode(params):
    """Inverse: map a raw params dict back to a latent vector (best-effort).

    Missing keys default to the current TUNED_DEFAULTS value (latent 0.0).
    Extra keys are ignored so an older config can seed a newer parameter set.
    """
    x = []
    for name, default in TUNED_DEFAULTS.items():
        v = _lookup_flat(params, name, default)
        if v is None:
            v = default
        try:
            v = float(v)
        except (TypeError, ValueError):
            v = float(default)
        if v <= 0:
            x.append(-10.0)
        else:
            x.append(math.log(v / default))
    return x


def write_toml(params, path):
    """Write a partial [scorer] TOML file; missing keys use compiled-in defaults."""
    with open(path, "w") as f:
        f.write("# Generated by CMA-ES tuner\n")
        f.write("[scorer]\n")
        for name, value in params.items():
            if name.startswith("pieces."):
                continue
            f.write(f"{name} = {value}\n")
        f.write("\n[scorer.pieces]\n")
        for name, value in params.items():
            if name.startswith("pieces."):
                piece_name = name.split(".", 1)[1]
                f.write(f"{piece_name} = {value}\n")
