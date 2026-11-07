"""
End-to-End Pipeline Verification & Synthetic Battery Pulse Simulator
--------------------------------------------------------------------
Simulates the exact 3-state ESP32 single-resistor pulse protocol (Section 6:
2s High-Z OCV -> 1s OUTPUT LOW Pulse -> 5s High-Z Relaxation) across a
temperature sweep from 20 °C to 55 °C (5 °C steps), runs parameter extraction,
and generates calibration models & plots.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from src.calibrate_model import fit_calibration_and_ml
from src.extract_parameters import extract_randles_parameters, plot_parameterization_results


def generate_synthetic_pulse_df(
    v_ocv: float = 3.10,
    r_ref_ohm: float = 470.0,
    r_s_true: float = 12.5,
    r_ct_true: float = 18.0,
    c_dl_true: float = 0.015,
    warburg_k: float = 0.002,
    noise_std_v: float = 0.0004,
    seed: int = 42,
) -> pd.DataFrame:
    """
    Generates a synthetic ESP32 ADC dataset matching the exact format output by
    `firmware/src/main.cpp`: columns `time_us,voltage_mv,raw_adc,phase`.
    """
    rng = np.random.default_rng(seed)

    # Phase 0: 0 to 2.0s (High-Z OCV)
    t0 = np.linspace(0.0, 2.0, 2000, endpoint=False)
    v0 = np.full_like(t0, v_ocv) + rng.normal(0.0, noise_std_v, size=len(t0))
    p0 = np.zeros_like(t0, dtype=int)

    # Phase 1: 2.0s to 3.0s (Pulse OUTPUT LOW through R_ref)
    # Include 40 kHz burst for first 5 ms + 1 kHz for remainder
    t1_burst = np.linspace(2.0, 2.005, 200, endpoint=False)
    t1_std = np.linspace(2.005, 3.0, 995)
    t1 = np.concatenate([t1_burst, t1_std])
    dt1 = t1 - 2.0

    # Voltage divider at t=0+: V(0+) = V_OCV * R_ref / (R_ref + R_s)
    v_0plus = v_ocv * (r_ref_ohm / (r_ref_ohm + r_s_true))
    i_approx = v_0plus / r_ref_ohm
    tau_true = r_ct_true * c_dl_true
    dv_rc = i_approx * r_ct_true
    v_inf = v_0plus - dv_rc

    v1 = v_inf + dv_rc * np.exp(-dt1 / tau_true) - warburg_k * np.sqrt(dt1)
    v1 += rng.normal(0.0, noise_std_v, size=len(t1))
    p1 = np.ones_like(t1, dtype=int)

    # Phase 2: 3.0s to 8.0s (Relaxation High-Z)
    t2_burst = np.linspace(3.0001, 3.005, 200, endpoint=False)
    t2_std = np.linspace(3.005, 8.0, 4995)
    t2 = np.concatenate([t2_burst, t2_std])
    dt2 = t2 - 3.0

    # At t=3.0+, Ohmic drop R_s disappears immediately when High-Z is restored
    v_relax_start = float(v1[-1]) + (v_ocv - v_0plus)
    v2 = v_ocv - (v_ocv - v_relax_start) * np.exp(-dt2 / tau_true)
    v2 += rng.normal(0.0, noise_std_v, size=len(t2))
    p2 = np.full_like(t2, 2, dtype=int)

    t_all = np.concatenate([t0, t1, t2])
    v_all = np.concatenate([v0, v1, v2])
    phase_all = np.concatenate([p0, p1, p2])

    df = pd.DataFrame(
        {
            "time_us": np.round(t_all * 1e6).astype(np.int64),
            "voltage_mv": np.round(v_all * 1e3, 3),
            "raw_adc": np.clip(np.round((v_all / 3.3) * 4095), 0, 4095).astype(int),
            "phase": phase_all,
        }
    )
    return df


def run_verification_suite() -> None:
    out_dir = Path("results/verification")
    out_dir.mkdir(parents=True, exist_ok=True)

    print("1. Simulating Temperature Sweep (20 °C -> 55 °C, step 5 °C) per Guide Section 6...")
    temps_c = np.arange(20.0, 56.0, 5.0)
    sweep_records = []

    for idx, temp in enumerate(temps_c):
        # Arrhenius-like decrease in internal resistances R_s and R_ct with temperature
        r_s_true = 18.0 * np.exp(-0.018 * (temp - 20.0))
        r_ct_true = 26.0 * np.exp(-0.024 * (temp - 20.0))
        c_dl_true = 0.012 + 0.00015 * (temp - 20.0)
        soh_true = 100.0 - 0.3 * idx

        df_sim = generate_synthetic_pulse_df(
            v_ocv=3.10,
            r_ref_ohm=470.0,
            r_s_true=r_s_true,
            r_ct_true=r_ct_true,
            c_dl_true=c_dl_true,
            seed=100 + idx,
        )

        params, curves = extract_randles_parameters(df_sim, r_ref_ohm=470.0)
        rec = params.to_dict()
        rec["reference_temp_c"] = float(temp)
        rec["reference_soh_pct"] = float(soh_true)
        rec["true_r_s_ohm"] = round(float(r_s_true), 4)
        rec["true_r_ct_ohm"] = round(float(r_ct_true), 4)
        sweep_records.append(rec)

        if temp == 25.0:
            df_sim.to_csv(out_dir / "sample_pulse_25C.csv", index=False)
            plot_parameterization_results(params, curves, out_dir / "sample_pulse_25C_plot.png")

    sweep_df = pd.DataFrame(sweep_records)
    sweep_df.to_csv(out_dir / "temperature_sweep_dataset.csv", index=False)

    print("2. Fitting Calibration Polynomial & Scikit-Learn Models...")
    cal_summary, cal_plot = fit_calibration_and_ml(sweep_df, output_dir=out_dir)

    print("\n=== VERIFICATION COMPLETE ===")
    print(json.dumps(cal_summary, indent=2))
    print(f"Artifacts saved in: {out_dir.resolve()}")


if __name__ == "__main__":
    run_verification_suite()
