"""
Layer 6: Calibration & Machine Learning Estimation (NumPy polyfit + Scikit-Learn)
---------------------------------------------------------------------------------
Maps the extracted ECM feature vector [R_s, R_ct, C_dl, tau] across
a temperature/SoH sweep (e.g. 20 °C to 55 °C in 5 °C steps, Section 6 Step 3 & 4)
to:
  1. 2nd-order polynomial calibration coefficients for direct ESP32 embedding (`CAL c2 c1 c0`)
  2. Multi-output Scikit-Learn regressor predicting Internal Core Temperature (T) and SoH (%)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Any, Tuple

import joblib
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, r2_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler


def fit_calibration_and_ml(
    sweep_df: pd.DataFrame,
    output_dir: str | Path = "results/calibration",
) -> Tuple[Dict[str, Any], Path]:
    """
    Fits both an embedded polynomial calibration (Temp vs R_s) and a multivariate
    ML model ([R_s, R_ct, C_dl, tau] -> Temp_C, SoH_pct).
    """
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    r_s = sweep_df["r_s_ohm"].to_numpy(dtype=float)
    temp_c = sweep_df["reference_temp_c"].to_numpy(dtype=float)

    # 1. 2nd-Order Polynomial Calibration: Temp(C) = c2 * R_s^2 + c1 * R_s + c0
    c2, c1, c0 = [float(x) for x in np.polyfit(r_s, temp_c, deg=2)]
    temp_poly_pred = np.polyval([c2, c1, c0], r_s)
    poly_r2 = float(r2_score(temp_c, temp_poly_pred))
    poly_mae = float(mean_absolute_error(temp_c, temp_poly_pred))

    # 2. Multivariate Feature Matrix X = [R_s, R_ct, C_dl, tau]
    feature_cols = ["r_s_ohm", "r_ct_ohm", "c_dl_farad", "tau_sec"]
    X = sweep_df[feature_cols].to_numpy(dtype=float)

    ml_temp_model = make_pipeline(StandardScaler(), Ridge(alpha=0.1))
    ml_temp_model.fit(X, temp_c)
    temp_ml_pred = ml_temp_model.predict(X)

    summary: Dict[str, Any] = {
        "esp32_polynomial_calibration": {
            "equation": "Temp_C = c2 * R_s^2 + c1 * R_s + c0",
            "c2": round(c2, 6),
            "c1": round(c1, 6),
            "c0": round(c0, 6),
            "r2_score": round(poly_r2, 5),
            "mae_deg_c": round(poly_mae, 4),
            "esp32_serial_command": f"CAL {c2:.6f} {c1:.6f} {c0:.6f}",
        },
        "multivariate_ml_temperature": {
            "features": feature_cols,
            "r2_score": round(float(r2_score(temp_c, temp_ml_pred)), 5),
            "mae_deg_c": round(float(mean_absolute_error(temp_c, temp_ml_pred)), 4),
        },
    }

    if "reference_soh_pct" in sweep_df.columns:
        soh = sweep_df["reference_soh_pct"].to_numpy(dtype=float)
        soh_model = RandomForestRegressor(n_estimators=100, random_state=42)
        soh_model.fit(X, soh)
        soh_pred = soh_model.predict(X)
        joblib.dump(soh_model, out_dir / "soh_rf_model.joblib")
        summary["multivariate_ml_soh"] = {
            "features": feature_cols,
            "r2_score": round(float(r2_score(soh, soh_pred)), 5),
            "mae_pct": round(float(mean_absolute_error(soh, soh_pred)), 4),
        }

    joblib.dump(ml_temp_model, out_dir / "temp_ridge_model.joblib")
    (out_dir / "calibration_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    # Plot calibration curves
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12.5, 5.0))

    r_grid = np.linspace(float(np.min(r_s)) * 0.95, float(np.max(r_s)) * 1.05, 100)
    ax1.scatter(r_s, temp_c, color="#d62728", s=45, label="Measured Sweep Data")
    ax1.plot(r_grid, np.polyval([c2, c1, c0], r_grid), "--", color="#1f77b4", lw=2.0, label="2nd-Order Polyfit")
    ax1.set_title("Core Temperature Calibration ($T$ vs. Ohmic $R_s$)")
    ax1.set_xlabel("Extracted $R_s$ ($\\Omega$)")
    ax1.set_ylabel("Reference Temperature (°C)")
    ax1.grid(True, linestyle=":", alpha=0.6)
    ax1.legend()

    ax2.plot(temp_c, sweep_df["r_ct_ohm"], "o-", color="#2ca02c", lw=1.8, label="$R_{ct}$ ($\\Omega$)")
    ax2_twin = ax2.twinx()
    ax2_twin.plot(temp_c, sweep_df["c_dl_farad"] * 1e3, "s--", color="#9467bd", lw=1.8, label="$C_{dl}$ (mF)")
    ax2.set_title("Electrochemical Interface Parameters ($R_{ct}$ & $C_{dl}$) vs. Temp")
    ax2.set_xlabel("Reference Temperature (°C)")
    ax2.set_ylabel("Charge Transfer Resistance $R_{ct}$ ($\\Omega$)", color="#2ca02c")
    ax2_twin.set_ylabel("Double-Layer Capacitance $C_{dl}$ (mF)", color="#9467bd")
    ax2.grid(True, linestyle=":", alpha=0.6)

    fig.tight_layout()
    plot_path = out_dir / "temperature_calibration_sweep.png"
    fig.savefig(plot_path, dpi=150)
    plt.close(fig)

    return summary, plot_path
