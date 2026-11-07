"""
Layer 3, 4 & 5: Comprehensive Battery ECM / Randles / EIS / DRT Parameter Extraction
------------------------------------------------------------------------------------
Extracts all time-domain, frequency-domain (EIS Impedance Spectrum), energetics, 2-RC Thevenin,
and Distribution of Relaxation Times (DRT) parameters from the single-resistor
tri-state pulse response WITHOUT requiring any hardware modifications:
  1. 1-RC + Warburg Randles Model (R_s, R_ct, C_dl, tau, Z_w)
  2. 2-RC Dual-Polarization Model (Fast SEI/interface tau_1 + Slow diffusion tau_2)
  3. Frequency-Domain EIS Spectrum Table (Freq, Re(Z), Im(Z), |Z|, Phase_deg, C_eff)
  4. Distribution of Relaxation Times (DRT gamma(tau) via Tikhonov regularization)
  5. Energetics & Power Capability (I(t), P(t), Charge mC/uAh, Energy mJ, P_max mW, R_DC)
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.integrate import trapezoid
from scipy.optimize import curve_fit
from scipy.signal import find_peaks


@dataclass
class BatteryParameters:
    # Basic & 1-RC Randles Parameters
    v_ocv_v: float
    v_min_pulse_v: float
    max_voltage_drop_mv: float
    i_pulse_mean_a: float
    i_pulse_mean_ma: float
    i_pulse_peak_ma: float
    r_ref_ohm: float
    r_s_ohm: float
    r_ct_ohm: float
    r_dc_total_ohm: float
    c_dl_farad: float
    tau_sec: float
    warburg_slope_v_s05: float
    r_ct_relax_ohm: float
    c_dl_relax_farad: float
    tau_relax_sec: float
    r_squared_pulse: float
    r_squared_relax: float

    # 2-RC Dual-Time-Constant Parameters (Fast Interface vs Slow Diffusion)
    r1_fast_ohm: float
    c1_fast_farad: float
    tau1_fast_sec: float
    r2_slow_ohm: float
    c2_slow_farad: float
    tau2_slow_sec: float
    r_squared_2rc_relax: float

    # Energetics & Power Metrics
    pulse_charge_mc: float
    pulse_capacity_uah: float
    pulse_energy_mj: float
    max_theoretical_power_mw: float

    # Frequency-Domain EIS Metrics
    eis_r0_ohm: Optional[float] = None
    eis_r1_ohm: Optional[float] = None
    eis_c1_farad: Optional[float] = None
    eis_harmonic_count: int = 0
    eis_mean_phase_deg: float = 0.0
    eis_min_phase_deg: float = 0.0
    eis_peak_phase_freq_hz: float = 0.0

    # DRT (Distribution of Relaxation Times) Peaks
    drt_peak1_tau_sec: Optional[float] = None
    drt_peak1_gamma_ohm: Optional[float] = None
    drt_peak2_tau_sec: Optional[float] = None
    drt_peak2_gamma_ohm: Optional[float] = None

    def to_feature_vector(self) -> list[float]:
        """Returns the primary 4D digital ECM feature vector [R_s, R_ct, C_dl, tau]."""
        return [self.r_s_ohm, self.r_ct_ohm, self.c_dl_farad, self.tau_sec]

    def to_extended_feature_vector(self) -> list[float]:
        """Returns the extended 10D feature vector for advanced ML models."""
        return [
            self.v_ocv_v,
            self.r_s_ohm,
            self.r_ct_ohm,
            self.c_dl_farad,
            self.tau_sec,
            self.r1_fast_ohm,
            self.tau1_fast_sec,
            self.r2_slow_ohm,
            self.tau2_slow_sec,
            self.eis_min_phase_deg,
        ]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def exp_rc_warburg_model(t: np.ndarray, v_inf: float, delta_v_rc: float, tau: float, k_w: float) -> np.ndarray:
    """1-RC + Warburg transient voltage response during pulse discharge."""
    return v_inf + delta_v_rc * np.exp(-t / np.maximum(tau, 1e-6)) - k_w * np.sqrt(np.maximum(t, 0.0))


def exp_relaxation_model(t: np.ndarray, v_inf: float, delta_v_rc: float, tau: float) -> np.ndarray:
    """1-RC exponential voltage recovery during High-Z relaxation."""
    return v_inf - delta_v_rc * np.exp(-t / np.maximum(tau, 1e-6))


def exp_2rc_relaxation_model(
    t: np.ndarray, v_inf: float, dv1: float, tau1: float, dv2: float, tau2: float
) -> np.ndarray:
    """
    2-RC Dual-Polarization relaxation model:
    V(t) = V_inf - dv1 * exp(-t / tau1) - dv2 * exp(-t / tau2)
    where tau1 captures fast SEI/electrode interface relaxation and tau2 captures slow ion diffusion.
    """
    return (
        v_inf
        - dv1 * np.exp(-t / np.maximum(tau1, 1e-5))
        - dv2 * np.exp(-t / np.maximum(tau2, 1e-4))
    )


def enrich_timeseries_dataframe(df: pd.DataFrame, r_ref_ohm: float, v_ocv: float) -> pd.DataFrame:
    """
    Adds derived physical time-series columns to the raw dataframe:
      - time_s, voltage_v, overpotential_mv, current_ma, power_mw,
        apparent_resistance_ohm, cumulative_charge_mc, cumulative_energy_mj
    """
    out = df.copy()
    out["time_s"] = np.round(out["time_us"].to_numpy(dtype=float) * 1e-6, 6)
    out["voltage_v"] = np.round(out["voltage_mv"].to_numpy(dtype=float) * 1e-3, 5)

    v_arr = out["voltage_v"].to_numpy(dtype=float)
    phase_arr = out["phase"].to_numpy(dtype=int)
    t_arr = out["time_s"].to_numpy(dtype=float)

    overpotential_v = np.maximum(v_ocv - v_arr, 0.0)
    out["overpotential_mv"] = np.round(overpotential_v * 1e3, 3)

    # Current flows through R_ref to 0V GND only during Phase 1 (OUTPUT LOW)
    current_a = np.where(phase_arr == 1, v_arr / r_ref_ohm, 0.0)
    power_w = v_arr * current_a

    out["current_ma"] = np.round(current_a * 1e3, 5)
    out["power_mw"] = np.round(power_w * 1e3, 5)

    with np.errstate(divide="ignore", invalid="ignore"):
        r_app = np.where((phase_arr == 1) & (current_a > 1e-7), overpotential_v / current_a, 0.0)
    out["apparent_resistance_ohm"] = np.round(r_app, 4)

    dt = np.diff(t_arr, prepend=t_arr[0])
    dt = np.maximum(dt, 0.0)
    out["cumulative_charge_mc"] = np.round(np.cumsum(current_a * dt) * 1e3, 5)
    out["cumulative_energy_mj"] = np.round(np.cumsum(power_w * dt) * 1e3, 5)
    return out


def compute_drt_spectrum(
    t_relax: np.ndarray,
    v_relax: np.ndarray,
    i_pulse_mean: float,
    n_taus: int = 60,
    reg_lambda: float = 1e-2,
) -> Tuple[np.ndarray, np.ndarray, list[Tuple[float, float]]]:
    """
    Computes the Distribution of Relaxation Times (DRT) gamma(ln tau)
    from the relaxation voltage transient using Tikhonov-regularized NNLS/Ridge inversion:
      V_inf - V(t) = I_pulse * sum_k gamma_k * exp(-t / tau_k) * d(ln tau)
    """
    tau_grid = np.logspace(-3.5, 1.2, n_taus)  # 0.3 ms to ~15.8 s
    d_ln_tau = float(np.log(tau_grid[1] / tau_grid[0]))

    # Subsample relaxation curve to ~250 points for fast matrix inversion
    if len(t_relax) > 250:
        idx = np.linspace(0, len(t_relax) - 1, 250).astype(int)
        t_sub = t_relax[idx]
        v_sub = v_relax[idx]
    else:
        t_sub = t_relax
        v_sub = v_relax

    v_inf = float(np.mean(v_relax[-20:])) if len(v_relax) >= 20 else float(v_relax[-1])
    dv_target = np.maximum(v_inf - v_sub, 0.0)
    r_target = dv_target / max(i_pulse_mean, 1e-6)

    # Basis matrix A[i, k] = exp(-t_i / tau_k) * d_ln_tau
    A = np.exp(-t_sub[:, None] / tau_grid[None, :]) * d_ln_tau

    # Second-order finite difference regularization matrix L
    L = np.eye(n_taus)
    for i in range(1, n_taus - 1):
        L[i, i - 1] = -0.5
        L[i, i] = 1.0
        L[i, i + 1] = -0.5

    # Solve (A^T A + lambda * L^T L) gamma = A^T r_target with non-negativity projection
    ATA = A.T @ A + reg_lambda * (L.T @ L)
    ATb = A.T @ r_target
    try:
        gamma_raw = np.linalg.solve(ATA, ATb)
    except np.linalg.LinAlgError:
        gamma_raw = np.zeros(n_taus)

    gamma = np.maximum(gamma_raw, 0.0)

    # Identify top 2 relaxation time peaks
    peaks_idx, _ = find_peaks(gamma, prominence=max(float(np.max(gamma)) * 0.05, 1e-6))
    peak_list: list[Tuple[float, float]] = []
    if len(peaks_idx) > 0:
        sorted_idx = sorted(peaks_idx, key=lambda idx_val: gamma[idx_val], reverse=True)
        for p_i in sorted_idx[:2]:
            peak_list.append((round(float(tau_grid[p_i]), 5), round(float(gamma[p_i]), 4)))
    elif float(np.max(gamma)) > 0:
        p_i = int(np.argmax(gamma))
        peak_list.append((round(float(tau_grid[p_i]), 5), round(float(gamma[p_i]), 4)))

    return tau_grid, gamma, peak_list


def extract_randles_parameters(
    df: pd.DataFrame,
    r_ref_ohm: float = 470.0,
    fit_eis: bool = True,
) -> Tuple[BatteryParameters, Dict[str, Any]]:
    """
    Extracts comprehensive Randles 1-RC, 2-RC Thevenin, Frequency-Domain CSI,
    DRT, and Energetics parameters from the pulse dataframe.
    """
    data = df.copy()
    if "time_s" not in data.columns:
        data["time_s"] = data["time_us"].to_numpy(dtype=float) * 1e-6
    if "voltage_v" not in data.columns:
        data["voltage_v"] = data["voltage_mv"].to_numpy(dtype=float) * 1e-3

    p0 = data[data["phase"] == 0]
    p1 = data[data["phase"] == 1]
    p2 = data[data["phase"] == 2]

    if p0.empty or p1.empty or p2.empty:
        raise ValueError("Dataframe must contain phases 0 (OCV), 1 (Pulse), and 2 (Relaxation).")

    # 1. Open Circuit Voltage (V_OCV)
    v_ocv = float(p0["voltage_v"].tail(max(10, len(p0) // 2)).mean())
    v_min_pulse = float(p1["voltage_v"].min())
    max_v_drop_mv = max((v_ocv - v_min_pulse) * 1e3, 0.0)

    # Enrich time-series dataframe with instantaneous current, power, charge, energy
    enriched_df = enrich_timeseries_dataframe(data, r_ref_ohm=r_ref_ohm, v_ocv=v_ocv)

    # 2. Instantaneous Ohmic Drop at t = 0+ (R_s)
    v_0plus = float(p1["voltage_v"].iloc[:5].mean())
    delta_v0 = max(v_ocv - v_0plus, 1e-5)
    i_0plus = max(v_0plus / r_ref_ohm, 1e-6)
    r_s = delta_v0 / i_0plus

    # Current & Energetics during Phase 1
    t_p1_raw = p1["time_s"].to_numpy(dtype=float)
    v_p1 = p1["voltage_v"].to_numpy(dtype=float)
    i_pulse_series = v_p1 / r_ref_ohm
    i_pulse_mean = float(np.mean(i_pulse_series))
    i_pulse_peak = float(np.max(i_pulse_series))

    pulse_charge_c = float(trapezoid(i_pulse_series, t_p1_raw))
    pulse_charge_mc = pulse_charge_c * 1e3
    pulse_capacity_uah = (pulse_charge_c / 3600.0) * 1e6
    pulse_energy_mj = float(trapezoid(v_p1 * i_pulse_series, t_p1_raw)) * 1e3
    max_theo_power_mw = ((v_ocv ** 2) / (4.0 * max(r_s, 1e-3))) * 1e3

    # 3. Curve fitting on Phase 1 (1-RC + Warburg Pulse Transient)
    t_p1 = t_p1_raw - float(t_p1_raw[0])
    v_end_p1 = float(v_p1[-1])
    dv_guess = max(v_0plus - v_end_p1, 1e-3)

    try:
        popt_p1, _ = curve_fit(
            exp_rc_warburg_model,
            t_p1,
            v_p1,
            p0=[v_end_p1, dv_guess, 0.15, 0.001],
            bounds=([0.0, 0.0, 1e-4, 0.0], [v_ocv, v_ocv, 30.0, 1.0]),
            maxfev=10000,
        )
        v_inf_p1, delta_v_rc, tau_p1, k_w = [float(x) for x in popt_p1]
        v_fit_p1 = exp_rc_warburg_model(t_p1, *popt_p1)
    except Exception:
        delta_v_rc = dv_guess
        tau_p1 = 0.15
        k_w = 0.0
        v_fit_p1 = np.full_like(t_p1, v_end_p1)

    ss_res_p1 = float(np.sum((v_p1 - v_fit_p1) ** 2))
    ss_tot_p1 = float(np.sum((v_p1 - np.mean(v_p1)) ** 2)) + 1e-12
    r2_p1 = 1.0 - (ss_res_p1 / ss_tot_p1)

    r_ct = delta_v_rc / max(i_pulse_mean, 1e-6)
    c_dl = tau_p1 / max(r_ct, 1e-6)

    # 4. Curve fitting on Phase 2 (1-RC & 2-RC Relaxation High-Z Recovery)
    t_p2_raw = p2["time_s"].to_numpy(dtype=float)
    t_p2 = t_p2_raw - float(t_p2_raw[0])
    v_p2 = p2["voltage_v"].to_numpy(dtype=float)
    v_0_relax = float(v_p2[:5].mean())
    v_end_relax = float(v_p2[-10:].mean())
    dv_relax_guess = max(v_end_relax - v_0_relax, 1e-3)

    try:
        popt_p2, _ = curve_fit(
            exp_relaxation_model,
            t_p2,
            v_p2,
            p0=[v_end_relax, dv_relax_guess, 0.25],
            bounds=([0.0, 0.0, 1e-4], [5.0, 5.0, 60.0]),
            maxfev=10000,
        )
        v_inf_p2, dv_rc_relax, tau_relax = [float(x) for x in popt_p2]
        v_fit_p2 = exp_relaxation_model(t_p2, *popt_p2)
    except Exception:
        dv_rc_relax = dv_relax_guess
        tau_relax = 0.25
        v_fit_p2 = np.full_like(t_p2, v_end_relax)

    ss_res_p2 = float(np.sum((v_p2 - v_fit_p2) ** 2))
    ss_tot_p2 = float(np.sum((v_p2 - np.mean(v_p2)) ** 2)) + 1e-12
    r2_p2 = 1.0 - (ss_res_p2 / ss_tot_p2)

    r_ct_relax = dv_rc_relax / max(i_pulse_mean, 1e-6)
    c_dl_relax = tau_relax / max(r_ct_relax, 1e-6)

    # 4b. 2-RC Dual-Time-Constant Fit (Fast tau1 + Slow tau2)
    try:
        popt_2rc, _ = curve_fit(
            exp_2rc_relaxation_model,
            t_p2,
            v_p2,
            p0=[v_end_relax, dv_relax_guess * 0.4, 0.015, dv_relax_guess * 0.6, 0.45],
            bounds=([0.0, 0.0, 1e-4, 0.0, 0.02], [5.0, 5.0, 0.15, 5.0, 60.0]),
            maxfev=15000,
        )
        _, dv1_fit, tau1_fit, dv2_fit, tau2_fit = [float(x) for x in popt_2rc]
        # Ensure tau1 is always the faster time constant
        if tau1_fit > tau2_fit:
            dv1_fit, dv2_fit = dv2_fit, dv1_fit
            tau1_fit, tau2_fit = tau2_fit, tau1_fit
        v_fit_2rc = exp_2rc_relaxation_model(t_p2, *popt_2rc)
    except Exception:
        dv1_fit = dv_rc_relax * 0.3
        tau1_fit = 0.01
        dv2_fit = dv_rc_relax * 0.7
        tau2_fit = tau_relax
        v_fit_2rc = v_fit_p2

    ss_res_2rc = float(np.sum((v_p2 - v_fit_2rc) ** 2))
    r2_2rc = 1.0 - (ss_res_2rc / ss_tot_p2)
    r1_fast = dv1_fit / max(i_pulse_mean, 1e-6)
    c1_fast = tau1_fit / max(r1_fast, 1e-6)
    r2_slow = dv2_fit / max(i_pulse_mean, 1e-6)
    c2_slow = tau2_fit / max(r2_slow, 1e-6)

    # 5. Distribution of Relaxation Times (DRT)
    tau_drt, gamma_drt, drt_peaks = compute_drt_spectrum(t_p2, v_p2, i_pulse_mean)
    drt_df = pd.DataFrame({"tau_sec": np.round(tau_drt, 6), "gamma_ohm": np.round(gamma_drt, 5)})

    # 6. Frequency-Domain FFT -> Complex Impedance Z(f), Phase Angle, & EIS Spectrum Table
    freqs, z_complex, eis_params, eis_df = compute_fft_impedance(
        data, r_ref_ohm, r_s, r_ct, c_dl, fit_eis=fit_eis
    )

    if not eis_df.empty:
        eis_count = len(eis_df)
        eis_mean_phase = float(eis_df["z_phase_deg"].mean())
        min_idx = int(eis_df["z_phase_deg"].idxmin())
        eis_min_phase = float(eis_df.loc[min_idx, "z_phase_deg"])
        eis_peak_freq = float(eis_df.loc[min_idx, "freq_hz"])
    else:
        eis_count = 0
        eis_mean_phase = 0.0
        eis_min_phase = 0.0
        eis_peak_freq = 0.0

    params = BatteryParameters(
        v_ocv_v=round(v_ocv, 5),
        v_min_pulse_v=round(v_min_pulse, 5),
        max_voltage_drop_mv=round(max_v_drop_mv, 3),
        i_pulse_mean_a=round(i_pulse_mean, 6),
        i_pulse_mean_ma=round(i_pulse_mean * 1e3, 4),
        i_pulse_peak_ma=round(i_pulse_peak * 1e3, 4),
        r_ref_ohm=round(r_ref_ohm, 2),
        r_s_ohm=round(r_s, 4),
        r_ct_ohm=round(r_ct, 4),
        r_dc_total_ohm=round(r_s + r_ct, 4),
        c_dl_farad=round(c_dl, 6),
        tau_sec=round(tau_p1, 5),
        warburg_slope_v_s05=round(k_w, 6),
        r_ct_relax_ohm=round(r_ct_relax, 4),
        c_dl_relax_farad=round(c_dl_relax, 6),
        tau_relax_sec=round(tau_relax, 5),
        r_squared_pulse=round(r2_p1, 5),
        r_squared_relax=round(r2_p2, 5),
        r1_fast_ohm=round(r1_fast, 4),
        c1_fast_farad=round(c1_fast, 6),
        tau1_fast_sec=round(tau1_fit, 5),
        r2_slow_ohm=round(r2_slow, 4),
        c2_slow_farad=round(c2_slow, 6),
        tau2_slow_sec=round(tau2_fit, 5),
        r_squared_2rc_relax=round(r2_2rc, 5),
        pulse_charge_mc=round(pulse_charge_mc, 4),
        pulse_capacity_uah=round(pulse_capacity_uah, 5),
        pulse_energy_mj=round(pulse_energy_mj, 4),
        max_theoretical_power_mw=round(max_theo_power_mw, 3),
        eis_r0_ohm=eis_params.get("R0"),
        eis_r1_ohm=eis_params.get("R1"),
        eis_c1_farad=eis_params.get("C1"),
        eis_harmonic_count=eis_count,
        eis_mean_phase_deg=round(eis_mean_phase, 3),
        eis_min_phase_deg=round(eis_min_phase, 3),
        eis_peak_phase_freq_hz=round(eis_peak_freq, 3),
        drt_peak1_tau_sec=drt_peaks[0][0] if len(drt_peaks) >= 1 else None,
        drt_peak1_gamma_ohm=drt_peaks[0][1] if len(drt_peaks) >= 1 else None,
        drt_peak2_tau_sec=drt_peaks[1][0] if len(drt_peaks) >= 2 else None,
        drt_peak2_gamma_ohm=drt_peaks[1][1] if len(drt_peaks) >= 2 else None,
    )

    curves: Dict[str, Any] = {
        "enriched_df": enriched_df,
        "eis_df": eis_df,
        "drt_df": drt_df,
        "t_all": data["time_s"].to_numpy(dtype=float),
        "v_all": data["voltage_v"].to_numpy(dtype=float),
        "i_all_ma": enriched_df["current_ma"].to_numpy(dtype=float),
        "t_p1_abs": p1["time_s"].to_numpy(dtype=float),
        "v_fit_p1": v_fit_p1,
        "t_p2_abs": p2["time_s"].to_numpy(dtype=float),
        "v_fit_p2": v_fit_p2,
        "v_fit_2rc": v_fit_2rc,
        "freqs": freqs,
        "z_complex": z_complex,
        "tau_drt": tau_drt,
        "gamma_drt": gamma_drt,
    }
    return params, curves


def compute_fft_impedance(
    data: pd.DataFrame,
    r_ref_ohm: float,
    r_s_init: float,
    r_ct_init: float,
    c_dl_init: float,
    fit_eis: bool = True,
) -> Tuple[np.ndarray, np.ndarray, Dict[str, Optional[float]], pd.DataFrame]:
    """
    Computes frequency-domain impedance Z(f) = -Delta_V(f) / I(f) via FFT,
    builds the EIS Spectrum DataFrame (Amplitude, Phase_deg, Re, Im, C_eff),
    and fits a Randles circuit R0-p(R1,C1) using `impedance.py`.
    """
    t = data["time_s"].to_numpy(dtype=float)
    v = data["voltage_v"].to_numpy(dtype=float)
    phase = data["phase"].to_numpy(dtype=int)

    v_ocv = float(np.mean(v[phase == 0]))
    i_trace = np.where(phase == 1, v / r_ref_ohm, 0.0)

    n_grid = 4096
    t_uni = np.linspace(t[0], t[-1], n_grid)
    dt = float(t_uni[1] - t_uni[0])
    dv_uni = np.interp(t_uni, t, v_ocv - v)
    i_uni = np.interp(t_uni, t, i_trace)

    V_fft = np.fft.rfft(dv_uni)
    I_fft = np.fft.rfft(i_uni)
    freqs = np.fft.rfftfreq(n_grid, d=dt)

    valid = (freqs > 0.1) & (freqs <= 200.0) & (np.abs(I_fft) > 1e-3 * np.max(np.abs(I_fft)))
    freqs_sel = freqs[valid]
    z_sel = V_fft[valid] / I_fft[valid]

    keep = (np.real(z_sel) > 0) & (np.imag(z_sel) <= 0)
    if np.sum(keep) >= 5:
        freqs_sel = freqs_sel[keep]
        z_sel = z_sel[keep]

    eis_result: Dict[str, Optional[float]] = {"R0": None, "R1": None, "C1": None}
    if fit_eis and len(freqs_sel) >= 5:
        try:
            from impedance.models.circuits import CustomCircuit

            circuit = CustomCircuit(
                circuit="R0-p(R1,C1)",
                initial_guess=[max(r_s_init, 0.01), max(r_ct_init, 0.01), max(c_dl_init, 1e-4)],
            )
            circuit.fit(freqs_sel, z_sel)
            r0_fit, r1_fit, c1_fit = [float(x) for x in circuit.parameters_]
            eis_result = {
                "R0": round(r0_fit, 4),
                "R1": round(r1_fit, 4),
                "C1": round(c1_fit, 6),
            }
        except Exception:
            pass

    if len(freqs_sel) > 0:
        z_real = np.real(z_sel)
        z_imag = np.imag(z_sel)
        z_mag = np.abs(z_sel)
        z_phase_deg = np.degrees(np.arctan2(z_imag, z_real))
        with np.errstate(divide="ignore", invalid="ignore"):
            c_eff = np.where(np.abs(z_imag) > 1e-9, -1.0 / (2.0 * np.pi * freqs_sel * z_imag), 0.0)

        eis_df = pd.DataFrame(
            {
                "harmonic_idx": np.arange(1, len(freqs_sel) + 1),
                "freq_hz": np.round(freqs_sel, 4),
                "z_real_ohm": np.round(z_real, 4),
                "z_imag_ohm": np.round(z_imag, 4),
                "z_magnitude_ohm": np.round(z_mag, 4),
                "z_phase_deg": np.round(z_phase_deg, 3),
                "effective_capacitance_farad": np.round(c_eff, 6),
            }
        )
    else:
        eis_df = pd.DataFrame(
            columns=[
                "harmonic_idx",
                "freq_hz",
                "z_real_ohm",
                "z_imag_ohm",
                "z_magnitude_ohm",
                "z_phase_deg",
                "effective_capacitance_farad",
            ]
        )

    return freqs_sel, z_sel, eis_result, eis_df


def save_all_run_outputs(
    params: BatteryParameters,
    curves: Dict[str, Any],
    run_dir: str | Path,
    stem: str,
    extra_meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Path]:
    """Saves enriched CSV, EIS spectrum CSV, DRT CSV, JSON parameters, and 4-panel plot."""
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)

    csv_path = run_dir / f"{stem}.csv"
    eis_path = run_dir / f"{stem}_eis_spectrum.csv"
    drt_path = run_dir / f"{stem}_drt.csv"
    json_path = run_dir / f"{stem}_params.json"
    plot_path = run_dir / f"{stem}_plot.png"

    curves["enriched_df"].to_csv(csv_path, index=False)
    curves["eis_df"].to_csv(eis_path, index=False)
    curves["drt_df"].to_csv(drt_path, index=False)

    result_dict = params.to_dict()
    if extra_meta:
        result_dict.update(extra_meta)
    json_path.write_text(json.dumps(result_dict, indent=2), encoding="utf-8")

    plot_parameterization_results(params, curves, plot_path)
    return {
        "csv": csv_path,
        "eis_csv": eis_path,
        "drt_csv": drt_path,
        "json": json_path,
        "plot": plot_path,
    }


def plot_parameterization_results(
    params: BatteryParameters,
    curves: Dict[str, Any],
    output_path: str | Path,
) -> Path:
    """
    Generates a 4-panel diagnostic dashboard:
      1. Time-Domain Voltage & 1-RC/2-RC Fits
      2. Nyquist Complex Impedance Spectrum (-Im(Z) vs Re(Z))
      3. Bode Magnitude (|Z|) & Impedance Phase Angle (deg) vs Frequency
      4. Distribution of Relaxation Times (DRT gamma(tau) vs tau)
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    fig, axes = plt.subplots(2, 2, figsize=(14.5, 10.0))
    ax1, ax2 = axes[0, 0], axes[0, 1]
    ax3, ax4 = axes[1, 0], axes[1, 1]

    # Panel 1: Time-domain voltage transient & curve fits
    ax1.plot(curves["t_all"], curves["v_all"], label="Measured $V_{bat}(t)$", color="#1f77b4", alpha=0.8, lw=1.2)
    ax1.plot(curves["t_p1_abs"], curves["v_fit_p1"], "--", label="Pulse RC+Warburg Fit", color="#d62728", lw=2.0)
    ax1.plot(curves["t_p2_abs"], curves["v_fit_p2"], "--", label="Relaxation 1-RC Fit", color="#2ca02c", lw=1.8)
    if "v_fit_2rc" in curves:
        ax1.plot(curves["t_p2_abs"], curves["v_fit_2rc"], ":", label="Relaxation 2-RC Fit", color="#9467bd", lw=2.0)
    ax1.set_title("1. Single-Resistor Tri-State Pulse & Relaxation")
    ax1.set_xlabel("Time (s)")
    ax1.set_ylabel("Battery Terminal Voltage (V)")
    ax1.grid(True, linestyle=":", alpha=0.6)
    ax1.legend(loc="lower right", fontsize=8.5)

    info_text = (
        f"$V_{{OCV}}$ = {params.v_ocv_v:.3f} V | $I_{{pulse}}$ = {params.i_pulse_mean_ma:.2f} mA\n"
        f"$R_s$ = {params.r_s_ohm:.2f} $\\Omega$ | $R_{{ct}}$ = {params.r_ct_ohm:.2f} $\\Omega$\n"
        f"$C_{{dl}}$ = {params.c_dl_farad*1e3:.2f} mF | $\\tau$ = {params.tau_sec*1e3:.1f} ms\n"
        f"2-RC: $\\tau_1$={params.tau1_fast_sec*1e3:.1f} ms, $\\tau_2$={params.tau2_slow_sec*1e3:.1f} ms\n"
        f"$P_{{max}}$ = {params.max_theoretical_power_mw:.1f} mW"
    )
    ax1.text(
        0.03,
        0.05,
        info_text,
        transform=ax1.transAxes,
        fontsize=8.8,
        verticalalignment="bottom",
        bbox=dict(boxstyle="round,pad=0.4", facecolor="#f8f9fa", edgecolor="#ced4da"),
    )

    # Panel 2: Nyquist Plot (-Im(Z) vs Re(Z))
    z = curves["z_complex"]
    if len(z) > 0:
        ax2.scatter(np.real(z), -np.imag(z), color="#ff7f0e", s=24, alpha=0.85, label="FFT Harmonics $Z(\\omega)$")
    w_theo = np.logspace(-2, 3, 200)
    z_theo = params.r_s_ohm + params.r_ct_ohm / (1.0 + 1j * w_theo * params.r_ct_ohm * params.c_dl_farad)
    ax2.plot(np.real(z_theo), -np.imag(z_theo), "-", color="#1f77b4", lw=2.0, label="Fitted Randles Semicircle")
    ax2.set_title("2. Nyquist Impedance Spectrum (Randles ECM)")
    ax2.set_xlabel("$Z_{real}$ ($\\Omega$)")
    ax2.set_ylabel("$-Z_{imag}$ ($\\Omega$)")
    ax2.grid(True, linestyle=":", alpha=0.6)
    ax2.legend(loc="upper right", fontsize=8.5)

    # Panel 3: Bode Plot (|Z| Magnitude and Phase Angle vs Frequency)
    freqs = curves["freqs"]
    if len(freqs) > 0 and len(z) > 0:
        z_mag = np.abs(z)
        z_phase = np.degrees(np.arctan2(np.imag(z), np.real(z)))
        ax3.semilogx(freqs, z_mag, "o-", color="#1f77b4", ms=3.5, lw=1.2, alpha=0.85, label="$|Z(f)|$ ($\\Omega$)")
        ax3_twin = ax3.twinx()
        ax3_twin.semilogx(freqs, z_phase, "s--", color="#d62728", ms=3.5, lw=1.2, alpha=0.85, label="Phase $\\angle Z(f)$ (°)")
        ax3_twin.set_ylabel("Impedance Phase Angle $\\angle Z(f)$ (Degrees)", color="#d62728")
    else:
        f_grid = w_theo / (2.0 * np.pi)
        ax3.semilogx(f_grid, np.abs(z_theo), "-", color="#1f77b4", lw=1.8, label="$|Z(f)|$ ($\\Omega$)")
        ax3_twin = ax3.twinx()
        ax3_twin.semilogx(f_grid, np.degrees(np.angle(z_theo)), "--", color="#d62728", lw=1.8, label="Phase (°)")
        ax3_twin.set_ylabel("Impedance Phase Angle $\\angle Z(f)$ (Degrees)", color="#d62728")
    ax3.set_title("3. Bode Magnitude & Impedance Phase Angle Spectrum")
    ax3.set_xlabel("Frequency (Hz)")
    ax3.set_ylabel("Impedance Magnitude $|Z(f)|$ ($\\Omega$)", color="#1f77b4")
    ax3.grid(True, which="both", linestyle=":", alpha=0.6)

    # Panel 4: Distribution of Relaxation Times (DRT)
    if "tau_drt" in curves and "gamma_drt" in curves:
        ax4.semilogx(curves["tau_drt"], curves["gamma_drt"], "-", color="#2ca02c", lw=2.2, label="DRT $\\gamma(\\ln \\tau)$")
        ax4.fill_between(curves["tau_drt"], 0, curves["gamma_drt"], color="#2ca02c", alpha=0.2)
        if params.drt_peak1_tau_sec is not None:
            ax4.axvline(params.drt_peak1_tau_sec, color="#d62728", linestyle="--", lw=1.4,
                        label=f"Peak 1: $\\tau_1$={params.drt_peak1_tau_sec*1e3:.1f} ms")
        if params.drt_peak2_tau_sec is not None:
            ax4.axvline(params.drt_peak2_tau_sec, color="#9467bd", linestyle="--", lw=1.4,
                        label=f"Peak 2: $\\tau_2$={params.drt_peak2_tau_sec*1e3:.1f} ms")
    ax4.set_title("4. Distribution of Relaxation Times (DRT Spectrum)")
    ax4.set_xlabel("Relaxation Time Constant $\\tau$ (s)")
    ax4.set_ylabel("DRT Polarization Density $\\gamma(\\ln \\tau)$ ($\\Omega$)")
    ax4.grid(True, which="both", linestyle=":", alpha=0.6)
    ax4.legend(loc="upper right", fontsize=8.5)

    fig.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)
    return output_path


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Extract comprehensive ECM/EIS/DRT parameters from pulse CSV.")
    parser.add_argument("csv_file", help="Path to pulse CSV file recorded from ESP32 or Oscilloscope")
    parser.add_argument("--r-ref", type=float, default=470.0, help="Reference resistor value in Ohms")
    parser.add_argument("--out-dir", default=None, help="Output directory (defaults to same folder as CSV)")
    args = parser.parse_args()

    csv_in_path = Path(args.csv_file)
    df_in = pd.read_csv(csv_in_path, comment="#")
    params_out, curves_out = extract_randles_parameters(df_in, r_ref_ohm=args.r_ref)

    target_dir = Path(args.out_dir) if args.out_dir else csv_in_path.parent
    paths = save_all_run_outputs(params_out, curves_out, target_dir, csv_in_path.stem)

    print(json.dumps(params_out.to_dict(), indent=2))
    for k, p in paths.items():
        print(f"Saved {k:8s} -> {p}")
