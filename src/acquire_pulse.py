"""
Layer 2: Lab Automation & Data Acquisition (PySerial + PyVISA)
--------------------------------------------------------------
Controls the ESP32 over USB Serial (and optionally a Digital Oscilloscope via VISA)
to trigger the 3-state single-resistor square-wave pulse protocol (2s High-Z -> 1s LOW -> 5s High-Z),
stream the recorded waveform to the PC, and immediately run ECM parameter extraction.
"""

from __future__ import annotations

import argparse
import io
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

import numpy as np
import pandas as pd
import serial
import serial.tools.list_ports

from src.extract_parameters import (
    extract_randles_parameters,
    plot_parameterization_results,
    save_all_run_outputs,
)


def auto_detect_esp32_port() -> Optional[str]:
    """Scans available serial ports for common ESP32 USB-UART bridges (CP210x, CH340, FTDI, USB JTAG)."""
    ports = list(serial.tools.list_ports.comports())
    esp_keywords = ("cp210", "ch340", "ch9102", "ftdi", "usb serial", "uart", "esp32", "silicon labs")
    for p in ports:
        desc = f"{p.description} {p.manufacturer or ''}".lower()
        if any(k in desc for k in esp_keywords):
            return p.device
    return ports[0].device if ports else None


def capture_oscilloscope_waveform(visa_resource: str = "AUTO", channel: str = "CHAN1") -> Optional[pd.DataFrame]:
    """
    Optional hybrid lab mode (Section 4 - Mode 2):
    Reads high-speed voltage waveform from a PyVISA-compatible Digital Oscilloscope.
    """
    try:
        import pyvisa

        rm = pyvisa.ResourceManager()
        resources = rm.list_resources()
        if not resources:
            print("[PyVISA] No VISA instruments detected.")
            return None
        target = resources[0] if visa_resource == "AUTO" else visa_resource
        inst = rm.open_resource(target)
        inst.timeout = 10000

        idn = inst.query("*IDN?").strip()
        print(f"[PyVISA] Connected to Oscilloscope: {idn}")

        inst.write(f":WAV:SOUR {channel}")
        inst.write(":WAV:MODE NORM")
        inst.write(":WAV:FORM ASC")
        xinc = float(inst.query(":WAV:XINC?"))
        raw_str = inst.query(":WAV:DATA?")

        # Strip IEEE 488.2 header if present
        if raw_str.startswith("#"):
            ndigits = int(raw_str[1])
            raw_str = raw_str[2 + ndigits :]

        voltages_v = np.fromstring(raw_str, sep=",")
        times_us = np.arange(len(voltages_v)) * xinc * 1e6
        df_scope = pd.DataFrame(
            {
                "time_us": times_us.astype(np.int64),
                "voltage_mv": (voltages_v * 1000.0).astype(np.float64),
            }
        )
        return df_scope
    except Exception as exc:
        print(f"[PyVISA] Warning: Oscilloscope capture skipped ({exc})")
        return None


def trigger_and_acquire_esp32(
    port: str,
    baud_rate: int = 921600,
    ocv_ms: int = 2000,
    pulse_ms: int = 1000,
    relax_ms: int = 5000,
    sample_period_us: int = 1000,
    r_ref_ohm: float = 470.0,
) -> Tuple[pd.DataFrame, dict]:
    """
    Connects to ESP32, configures R_ref, triggers the PULSE command,
    and parses the streamed CSV buffer into a pandas DataFrame.
    """
    total_wait_s = (ocv_ms + pulse_ms + relax_ms) / 1000.0 + 10.0
    with serial.Serial(port, baud_rate, timeout=total_wait_s) as ser:
        time.sleep(0.5)
        ser.reset_input_buffer()

        # Sync reference resistor value
        ser.write(f"RREF {r_ref_ohm:.2f}\n".encode("ascii"))
        time.sleep(0.1)
        while ser.in_waiting:
            line = ser.readline().decode("utf-8", errors="replace").strip()
            if line:
                print(f"[ESP32] {line}")

        cmd = f"PULSE {ocv_ms} {pulse_ms} {relax_ms} {sample_period_us}\n"
        print(f"[ESP32] Sending trigger command: {cmd.strip()}")
        ser.write(cmd.encode("ascii"))

        csv_lines: list[str] = []
        meta: dict = {"r_ref_ohm": r_ref_ohm}
        in_data_block = False

        t_deadline = time.time() + total_wait_s
        while time.time() < t_deadline:
            raw_line = ser.readline()
            if not raw_line:
                continue
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line:
                continue

            if line.startswith("# STATUS:"):
                print(f"[ESP32] {line}")
            elif line.startswith("# BEGIN_PULSE_DATA"):
                in_data_block = True
                print("[ESP32] Pulse complete. Receiving RAM buffer over Serial...")
            elif line.startswith("# META:"):
                meta_str = line.replace("# META:", "").strip()
                for kv in meta_str.split(","):
                    if "=" in kv:
                        k, v = kv.split("=", 1)
                        try:
                            meta[k.strip()] = float(v) if "." in v else int(v)
                        except ValueError:
                            meta[k.strip()] = v
            elif line.startswith("# END_PULSE_DATA"):
                print(f"[ESP32] Received {len(csv_lines) - 1} samples successfully.")
                break
            elif in_data_block and not line.startswith("#"):
                csv_lines.append(line)

        if len(csv_lines) < 2:
            raise RuntimeError("Did not receive valid pulse CSV data from ESP32.")

        df = pd.read_csv(io.StringIO("\n".join(csv_lines)))
        return df, meta


def main() -> None:
    config_path = Path("config.json")
    cfg = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}

    hw_cfg = cfg.get("hardware", {})
    proto_cfg = cfg.get("protocol", {})
    ser_cfg = cfg.get("serial", {})

    parser = argparse.ArgumentParser(description="Trigger ESP32 Battery Pulse & Extract Parameters")
    parser.add_argument("--port", default=ser_cfg.get("port", "AUTO"), help="Serial port (e.g. COM3 or AUTO)")
    parser.add_argument("--baud", type=int, default=ser_cfg.get("baud_rate", 921600), help="Serial baud rate")
    parser.add_argument("--r-ref", type=float, default=hw_cfg.get("r_ref_ohm", 470.0), help="Reference resistor (Ohm)")
    parser.add_argument("--temp-c", type=float, default=None, help="Ground-truth reference temperature (C) for sweep")
    parser.add_argument("--soc-pct", type=float, default=None, help="Battery State-of-Charge (%) for sweep")
    parser.add_argument("--out-dir", default="data/runs", help="Directory to store raw CSV, JSON, and plots")
    args = parser.parse_args()

    port = auto_detect_esp32_port() if args.port.upper() == "AUTO" else args.port
    if not port:
        print(
            "[!] No ESP32 serial port detected yet.\n"
            "    -> Plug in your ESP32 via USB and re-run, or run `python -m src.simulate_and_verify` "
            "to test the full software pipeline offline."
        )
        return

    ocv_ms = int(proto_cfg.get("pre_pulse_ocv_sec", 2.0) * 1000)
    pulse_ms = int(proto_cfg.get("pulse_duration_sec", 1.0) * 1000)
    relax_ms = int(proto_cfg.get("relaxation_sec", 5.0) * 1000)
    period_us = int(1e6 / proto_cfg.get("sampling_rate_hz", 1000))

    df, meta = trigger_and_acquire_esp32(
        port=port,
        baud_rate=args.baud,
        ocv_ms=ocv_ms,
        pulse_ms=pulse_ms,
        relax_ms=relax_ms,
        sample_period_us=period_us,
        r_ref_ohm=args.r_ref,
    )

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    tag = f"_T{args.temp_c:.1f}C" if args.temp_c is not None else ""
    stem = f"pulse_{timestamp}{tag}"
    run_dir = Path(args.out_dir) / f"run_{timestamp}{tag}"
    run_dir.mkdir(parents=True, exist_ok=True)

    params, curves = extract_randles_parameters(df, r_ref_ohm=args.r_ref)
    extra_meta = {}
    if args.temp_c is not None:
        extra_meta["reference_temp_c"] = args.temp_c
    if args.soc_pct is not None:
        extra_meta["reference_soc_pct"] = args.soc_pct

    paths = save_all_run_outputs(params, curves, run_dir, stem, extra_meta=extra_meta)

    result_dict = params.to_dict()
    result_dict.update(extra_meta)

    print("\n=== Extracted Battery Parameters ===")
    print(json.dumps(result_dict, indent=2))
    print(f"Saved Run Folder -> {run_dir}")
    for k, p in paths.items():
        print(f"  - {k:8s}: {p.name}")


if __name__ == "__main__":
    main()
