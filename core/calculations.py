from __future__ import annotations

from dataclasses import asdict

import numpy as np
import pandas as pd

from core.models import DerivedMetrics


GAMMA_CONF_HIGH_THRESHOLD = 0.67
GAMMA_CONF_MEDIUM_THRESHOLD = 0.40


def prepare_option_chain(raw: pd.DataFrame) -> pd.DataFrame:
    chain = raw.copy()
    if chain.empty:
        return chain
    numeric_fields = ["strike", "last", "change", "pct_change", "volume", "open_interest", "iv", "delta", "gamma", "vega", "theta", "multiplier"]
    for field in numeric_fields:
        if field in chain.columns:
            chain[field] = pd.to_numeric(chain[field], errors="coerce")
    chain["option_type"] = chain["option_type"].astype(str).str.upper()
    chain = chain.sort_values(["strike", "option_type"]).reset_index(drop=True)
    return chain


def find_atm_strike(option_chain: pd.DataFrame, spot: float) -> float | None:
    if option_chain.empty:
        return None
    strikes = option_chain["strike"].dropna().drop_duplicates()
    if strikes.empty:
        return None
    idx = (strikes - spot).abs().idxmin()
    return float(strikes.loc[idx])


def calculate_max_pain(option_chain: pd.DataFrame, use_multiplier: bool = True) -> float | None:
    if option_chain.empty:
        return None
    oi = pd.to_numeric(option_chain["open_interest"], errors="coerce")
    if oi.notna().sum() == 0 or oi.fillna(0).sum() <= 0:
        return None
    strikes = sorted(option_chain["strike"].dropna().unique().tolist())
    if not strikes:
        return None
    multiplier_series = option_chain["multiplier"].fillna(1) if use_multiplier else 1
    losses: dict[float, float] = {}
    for settle in strikes:
        call_loss = (((settle - option_chain["strike"]).clip(lower=0)) * option_chain["open_interest"] * multiplier_series).where(option_chain["option_type"] == "CALL", 0).sum()
        put_loss = (((option_chain["strike"] - settle).clip(lower=0)) * option_chain["open_interest"] * multiplier_series).where(option_chain["option_type"] == "PUT", 0).sum()
        losses[settle] = float(call_loss + put_loss)
    return min(losses, key=losses.get)


def calculate_oi_centers(option_chain: pd.DataFrame) -> tuple[float | None, float | None, float | None]:
    if option_chain.empty:
        return None, None, None
    oi = pd.to_numeric(option_chain["open_interest"], errors="coerce")
    if oi.notna().sum() == 0 or oi.fillna(0).sum() <= 0:
        return None, None, None
    grouped = option_chain.groupby(["strike", "option_type"], as_index=False)["open_interest"].sum()
    total = option_chain.groupby("strike", as_index=False)["open_interest"].sum()
    call = grouped[grouped["option_type"] == "CALL"]
    put = grouped[grouped["option_type"] == "PUT"]
    call_center = float(call.loc[call["open_interest"].idxmax(), "strike"]) if not call.empty else None
    put_center = float(put.loc[put["open_interest"].idxmax(), "strike"]) if not put.empty else None
    total_center = float(total.loc[total["open_interest"].idxmax(), "strike"]) if not total.empty else None
    return call_center, put_center, total_center


def compute_gamma_structure(
    option_rows: pd.DataFrame,
    spot: float,
    *,
    min_total_weight: float = 1.0,
    high_conf_threshold: float = GAMMA_CONF_HIGH_THRESHOLD,
    medium_conf_threshold: float = GAMMA_CONF_MEDIUM_THRESHOLD,
    peak_ratio_scale: float = 4.0,
    dispersion_scale: float = 1.35,
    sharpness_scale: float = 1.0,
) -> dict[str, float | str | None]:
    if option_rows.empty:
        return _empty_gamma_structure()

    required = option_rows.copy()
    required["strike"] = pd.to_numeric(required.get("strike"), errors="coerce")
    required["gamma"] = pd.to_numeric(required.get("gamma"), errors="coerce").abs()
    required["open_interest"] = pd.to_numeric(required.get("open_interest"), errors="coerce")
    required = required.dropna(subset=["strike"])
    required["open_interest"] = required["open_interest"].fillna(0.0)
    required["gamma_weight"] = required["gamma"].fillna(0.0) * required["open_interest"]

    grouped = required.groupby("strike", as_index=False)["gamma_weight"].sum()
    grouped = grouped[grouped["gamma_weight"] > 0].sort_values("strike").reset_index(drop=True)
    if grouped.empty:
        return _empty_gamma_structure()

    total_weight = float(grouped["gamma_weight"].sum())
    if total_weight < min_total_weight:
        result = _empty_gamma_structure()
        result["structure_label"] = "flat"
        return result

    grouped["distance_to_spot"] = (grouped["strike"] - spot).abs()
    barycenter = float((grouped["strike"] * grouped["gamma_weight"]).sum() / total_weight)

    ranked = grouped.sort_values(["gamma_weight", "distance_to_spot"], ascending=[False, True]).reset_index(drop=True)
    pin_strike = float(ranked.loc[0, "strike"])
    top_weights = ranked["gamma_weight"].to_numpy(dtype=float)
    top3_ratio = float(top_weights[:3].sum() / total_weight)
    peak_ratio = float(top_weights[0] / total_weight)
    top2 = float(top_weights[1]) if len(top_weights) > 1 else 0.0
    peak_sharpness = float(top_weights[0] / top2) if top2 > 0 else 10.0

    variance = float(((grouped["strike"] - barycenter) ** 2 * grouped["gamma_weight"]).sum() / total_weight)
    weighted_std = variance ** 0.5
    strike_range = float(grouped["strike"].max() - grouped["strike"].min())
    normalized_dispersion = float(weighted_std / strike_range) if strike_range > 0 else 0.0

    peak_component = clip01(peak_ratio * peak_ratio_scale)
    dispersion_component = clip01((1 - normalized_dispersion) * dispersion_scale)
    sharpness_component = clip01((peak_sharpness - 1.0) * sharpness_scale)
    confidence_score = (
        0.35 * clip01(top3_ratio)
        + 0.25 * peak_component
        + 0.20 * dispersion_component
        + 0.20 * sharpness_component
    )
    confidence_score = float(clip01(confidence_score))

    confidence_label = "HIGH" if confidence_score >= high_conf_threshold else "MEDIUM" if confidence_score >= medium_conf_threshold else "LOW"
    structure_label = _classify_gamma_structure(grouped, top3_ratio, peak_ratio, peak_sharpness, normalized_dispersion)
    if structure_label == "flat":
        confidence_label = "LOW"
        confidence_score = min(confidence_score, medium_conf_threshold - 0.01)
    elif structure_label == "diffuse" and confidence_label == "HIGH":
        confidence_label = "MEDIUM"
        confidence_score = min(confidence_score, high_conf_threshold - 0.01)

    return {
        # Gamma Barycenter is a warehouse-like gamma inventory centroid, not a trading mean-reversion anchor.
        "gamma_barycenter": barycenter,
        # Pin Strike is the more trade-relevant discrete strike where local gamma concentration is strongest.
        "pin_strike": pin_strike,
        "confidence_score": confidence_score,
        "confidence_label": confidence_label,
        "structure_label": structure_label,
        "top3_ratio": top3_ratio,
        "peak_ratio": peak_ratio,
        "weighted_std": weighted_std,
        "normalized_dispersion": normalized_dispersion,
        "peak_sharpness": peak_sharpness,
        "total_weight": total_weight,
    }


def _classify_gamma_structure(
    grouped: pd.DataFrame,
    top3_ratio: float,
    peak_ratio: float,
    peak_sharpness: float,
    normalized_dispersion: float,
) -> str:
    weights = grouped["gamma_weight"].to_numpy(dtype=float)
    if len(weights) <= 1:
        return "flat"
    weight_cv = float(np.std(weights) / np.mean(weights)) if np.mean(weights) > 0 else 0.0
    ranked = grouped.sort_values("gamma_weight", ascending=False).reset_index(drop=True)
    step = _estimate_strike_step(grouped["strike"].to_numpy(dtype=float))
    top1 = float(ranked.loc[0, "gamma_weight"])
    top2 = float(ranked.loc[1, "gamma_weight"]) if len(ranked) > 1 else 0.0
    top_distance = abs(float(ranked.loc[0, "strike"]) - float(ranked.loc[1, "strike"])) if len(ranked) > 1 else 0.0

    if weight_cv < 0.20 or (peak_ratio < 0.12 and peak_sharpness < 1.10):
        return "flat"
    if top2 > 0 and (top1 / top2) <= 1.20 and top_distance > max(step, 1e-9) * 1.5:
        return "bimodal"
    if peak_ratio >= 0.18 and top3_ratio >= 0.45 and normalized_dispersion <= 0.22:
        return "concentrated"
    return "diffuse"


def _empty_gamma_structure() -> dict[str, float | str | None]:
    return {
        "gamma_barycenter": None,
        "pin_strike": None,
        "confidence_score": 0.0,
        "confidence_label": "LOW",
        "structure_label": "flat",
        "top3_ratio": None,
        "peak_ratio": None,
        "weighted_std": None,
        "normalized_dispersion": None,
        "peak_sharpness": None,
        "total_weight": 0.0,
    }


def calculate_iv_metrics(option_chain: pd.DataFrame, atm_strike: float | None) -> tuple[float | None, float | None, float | None, float | None, float | None, float | None]:
    if option_chain.empty or atm_strike is None:
        return None, None, None, None, None, None
    iv_series = pd.to_numeric(option_chain.get("iv"), errors="coerce")
    if iv_series.notna().sum() == 0:
        return None, None, None, None, None, None
    atm_rows = option_chain[np.isclose(option_chain["strike"], atm_strike)]
    atm_iv = float(atm_rows["iv"].mean()) if not atm_rows.empty else None
    strikes = sorted(option_chain["strike"].dropna().unique())
    if len(strikes) < 3:
        return atm_iv, None, None, None, None, None
    step = abs(strikes[1] - strikes[0])
    put_target = atm_strike - 2 * step
    call_target = atm_strike + 2 * step
    put_iv = _mean_iv_for_strike(option_chain, put_target, "PUT")
    call_iv = _mean_iv_for_strike(option_chain, call_target, "CALL")
    skew = (put_iv - call_iv) if put_iv is not None and call_iv is not None else None
    call_iv_mean_3 = _mean_iv_near_atm(option_chain, atm_strike, "CALL")
    put_iv_mean_3 = _mean_iv_near_atm(option_chain, atm_strike, "PUT")
    return atm_iv, call_iv, put_iv, skew, call_iv_mean_3, put_iv_mean_3


def _mean_iv_for_strike(option_chain: pd.DataFrame, strike: float, option_type: str) -> float | None:
    rows = option_chain[np.isclose(option_chain["strike"], strike) & (option_chain["option_type"] == option_type)]
    return float(rows["iv"].mean()) if not rows.empty else None


def _mean_iv_near_atm(option_chain: pd.DataFrame, atm_strike: float, option_type: str) -> float | None:
    side = option_chain[option_chain["option_type"] == option_type].copy()
    if side.empty:
        return None
    side["strike"] = pd.to_numeric(side["strike"], errors="coerce")
    side["iv"] = pd.to_numeric(side["iv"], errors="coerce")
    side = side.dropna(subset=["strike", "iv"])
    if side.empty:
        return None
    nearest = side.assign(distance=(side["strike"] - atm_strike).abs()).sort_values(["distance", "strike"]).head(3)
    if nearest.empty:
        return None
    return float(nearest["iv"].mean())


def build_metrics(option_chain: pd.DataFrame, spot: float) -> DerivedMetrics:
    atm_strike = find_atm_strike(option_chain, spot)
    atm_iv, call_wing_iv, put_wing_iv, skew, atm_call_iv_mean_3, atm_put_iv_mean_3 = calculate_iv_metrics(option_chain, atm_strike)
    call_center, put_center, total_center = calculate_oi_centers(option_chain)
    gamma_structure = compute_gamma_structure(option_chain, spot)
    return DerivedMetrics(
        atm_strike=atm_strike,
        atm_iv=atm_iv,
        atm_call_iv_mean_3=atm_call_iv_mean_3,
        atm_put_iv_mean_3=atm_put_iv_mean_3,
        max_pain=calculate_max_pain(option_chain),
        call_oi_center=call_center,
        put_oi_center=put_center,
        total_oi_center=total_center,
        gamma_barycenter=_safe_float(gamma_structure["gamma_barycenter"]),
        pin_strike=_safe_float(gamma_structure["pin_strike"]),
        gamma_confidence_score=float(gamma_structure["confidence_score"] or 0.0),
        gamma_confidence_label=str(gamma_structure["confidence_label"]),
        gamma_structure_label=str(gamma_structure["structure_label"]),
        gamma_top3_ratio=_safe_float(gamma_structure["top3_ratio"]),
        gamma_peak_ratio=_safe_float(gamma_structure["peak_ratio"]),
        gamma_weighted_std=_safe_float(gamma_structure["weighted_std"]),
        gamma_normalized_dispersion=_safe_float(gamma_structure["normalized_dispersion"]),
        gamma_peak_sharpness=_safe_float(gamma_structure["peak_sharpness"]),
        call_wing_iv=call_wing_iv,
        put_wing_iv=put_wing_iv,
        skew=skew,
    )


def metrics_to_dict(metrics: DerivedMetrics) -> dict[str, float | str | None]:
    return asdict(metrics)


def clip01(value: float) -> float:
    if np.isnan(value):
        return 0.0
    return float(max(0.0, min(1.0, value)))


def _estimate_strike_step(strikes: np.ndarray) -> float:
    if len(strikes) < 2:
        return 1.0
    diffs = np.diff(np.sort(strikes))
    positive = diffs[diffs > 0]
    return float(np.min(positive)) if len(positive) else 1.0


def _safe_float(value: float | str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
