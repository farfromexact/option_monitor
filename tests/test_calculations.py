from __future__ import annotations

import pandas as pd

from core.calculations import calculate_max_pain, calculate_oi_centers, compute_gamma_structure, find_atm_strike


def sample_chain() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"strike": 2.4, "option_type": "CALL", "open_interest": 100, "multiplier": 10000},
            {"strike": 2.4, "option_type": "PUT", "open_interest": 200, "multiplier": 10000},
            {"strike": 2.45, "option_type": "CALL", "open_interest": 300, "multiplier": 10000},
            {"strike": 2.45, "option_type": "PUT", "open_interest": 400, "multiplier": 10000},
            {"strike": 2.5, "option_type": "CALL", "open_interest": 700, "multiplier": 10000},
            {"strike": 2.5, "option_type": "PUT", "open_interest": 100, "multiplier": 10000},
        ]
    )


def test_find_atm_strike() -> None:
    assert find_atm_strike(sample_chain(), 2.46) == 2.45


def test_calculate_max_pain() -> None:
    assert calculate_max_pain(sample_chain()) in {2.45, 2.5}


def test_calculate_oi_centers() -> None:
    call_center, put_center, total_center = calculate_oi_centers(sample_chain())
    assert call_center == 2.5
    assert put_center == 2.45
    assert total_center == 2.5


def test_compute_gamma_structure_concentrated() -> None:
    chain = pd.DataFrame(
        [
            {"strike": 2.4, "option_type": "CALL", "gamma": 0.01, "open_interest": 50},
            {"strike": 2.45, "option_type": "CALL", "gamma": 0.20, "open_interest": 500},
            {"strike": 2.45, "option_type": "PUT", "gamma": 0.18, "open_interest": 450},
            {"strike": 2.5, "option_type": "PUT", "gamma": 0.02, "open_interest": 60},
        ]
    )
    result = compute_gamma_structure(chain, 2.45)
    assert result["pin_strike"] == 2.45
    assert result["confidence_label"] in {"HIGH", "MEDIUM"}


def test_compute_gamma_structure_flat() -> None:
    chain = pd.DataFrame(
        [
            {"strike": 2.4, "option_type": "CALL", "gamma": 0.05, "open_interest": 100},
            {"strike": 2.45, "option_type": "CALL", "gamma": 0.05, "open_interest": 100},
            {"strike": 2.5, "option_type": "CALL", "gamma": 0.05, "open_interest": 100},
            {"strike": 2.55, "option_type": "CALL", "gamma": 0.05, "open_interest": 100},
        ]
    )
    result = compute_gamma_structure(chain, 2.45)
    assert result["confidence_label"] == "LOW"
    assert result["structure_label"] in {"flat", "diffuse"}
