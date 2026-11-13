"""
Desktop GUI for ESP32 Battery Square-Wave Current Pulse & EIS Parameterization.

Provides:
- Serial COM port detection, live OCV / PING probe, and optional PlatformIO firmware flash
- Configurable Reference Resistor (R_ref in Ohms) with quick presets for coin & AA/AAA cells
- Background pulse acquisition + automated parameter extraction (1-RC, 2-RC, EIS, DRT)
- Automatic switch to the Results & Plots tab upon completion, plus offline simulation
  and re-analysis of existing CSV captures.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import serial
import serial.tools.list_ports
from PIL import Image, ImageTk

from src.acquire_pulse import auto_detect_esp32_port, trigger_and_acquire_esp32
from src.extract_parameters import BatteryParameters, extract_randles_parameters, save_all_run_outputs
from src.simulate_and_verify import generate_synthetic_pulse_df

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.json"
RUNS_DIR = PROJECT_ROOT / "data" / "runs"

# Color palette (Dark Slate Studio)
BG_APP = "#0F172A"
BG_CARD = "#1E293B"
BG_INPUT = "#0B1220"
BORDER_SUBTLE = "#334155"
FG_PRIMARY = "#F8FAFC"
FG_MUTED = "#94A3B8"
ACCENT_BLUE = "#38BDF8"
ACCENT_BLUE_HOVER = "#0EA5E9"
ACCENT_GREEN = "#22C55E"
ACCENT_AMBER = "#F59E0B"
ACCENT_RED = "#EF4444"


def load_config() -> Dict[str, Any]:
    """Load hardware and protocol defaults from config.json."""
    default_cfg: Dict[str, Any] = {
        "hardware": {"r_ref_ohm": 470.0},
        "protocol": {"ocv_hold_sec": 2.0, "pulse_duration_sec": 1.0, "relax_sec": 5.0, "sample_rate_hz": 1000},
        "serial": {"port": "AUTO", "baud_rate": 921600, "timeout_sec": 15.0},
        "paths": {"runs_dir": "data/runs"},
    }
    if not CONFIG_PATH.exists():
        return default_cfg
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        for k, v in default_cfg.items():
            if k not in loaded or not isinstance(loaded[k], dict):
                loaded[k] = v
            else:
                for sk, sv in v.items():
                    loaded[k].setdefault(sk, sv)
        return loaded
    except Exception:
        return default_cfg


def save_r_ref_to_config(r_ref_ohm: float) -> None:
    """Persist the chosen reference resistor value into config.json."""
    cfg = load_config()
    cfg.setdefault("hardware", {})["r_ref_ohm"] = round(float(r_ref_ohm), 3)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


class BatteryPulseGUI:
    """Main desktop application controller."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("ESP32 Battery Pulse & EIS Parameterization Studio")
        self.root.geometry("1220x820")
        self.root.minsize(1040, 720)
        self.root.configure(bg=BG_APP)

        self.cfg = load_config()
        self.ui_queue: "queue.Queue[Tuple[str, Any]]" = queue.Queue()
        self.is_busy = False
        self.port_map: Dict[str, str] = {"AUTO (Auto-Detect ESP32)": "AUTO"}
        self.run_map: Dict[str, Path] = {}
        self.current_run_dir: Optional[Path] = None
        self.current_plot_path: Optional[Path] = None
        self.current_json_path: Optional[Path] = None
        self._pil_image: Optional[Image.Image] = None
        self._tk_image: Optional[ImageTk.PhotoImage] = None

        self._configure_styles()
        self._build_variables()
        self._build_layout()
        self.scan_ports(silent=True)
        self.refresh_runs_list(select_latest=True)
        self.root.after(100, self._poll_ui_queue)

    # ------------------------------------------------------------------
    # Styling & State Variables
    # ------------------------------------------------------------------
    def _configure_styles(self) -> None:
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure("TFrame", background=BG_APP)
        style.configure("Card.TFrame", background=BG_CARD, relief="flat")
        style.configure("TLabel", background=BG_CARD, foreground=FG_PRIMARY, font=("Segoe UI", 10))
        style.configure("AppTitle.TLabel", background=BG_APP, foreground=FG_PRIMARY, font=("Segoe UI", 16, "bold"))
        style.configure("AppSub.TLabel", background=BG_APP, foreground=FG_MUTED, font=("Segoe UI", 9))
        style.configure("CardTitle.TLabel", background=BG_CARD, foreground=ACCENT_BLUE, font=("Segoe UI", 11, "bold"))
        style.configure("Muted.TLabel", background=BG_CARD, foreground=FG_MUTED, font=("Segoe UI", 9))
        style.configure("MetricTitle.TLabel", background=BG_INPUT, foreground=FG_MUTED, font=("Segoe UI", 8, "bold"))
        style.configure("MetricVal.TLabel", background=BG_INPUT, foreground=FG_PRIMARY, font=("Segoe UI", 13, "bold"))
        style.configure("MetricSub.TLabel", background=BG_INPUT, foreground=ACCENT_BLUE, font=("Segoe UI", 8))

        style.configure("TNotebook", background=BG_APP, borderwidth=0)
        style.configure(
            "TNotebook.Tab",
            background=BG_CARD,
            foreground=FG_MUTED,
            padding=(16, 8),
            font=("Segoe UI", 10, "bold"),
            borderwidth=0,
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", ACCENT_BLUE_HOVER)],
            foreground=[("selected", "#FFFFFF")],
        )

        style.configure(
            "TCombobox",
            fieldbackground=BG_INPUT,
            background=BG_CARD,
            foreground=FG_PRIMARY,
            arrowcolor=FG_PRIMARY,
            bordercolor=BORDER_SUBTLE,
        )
        style.map("TCombobox", fieldbackground=[("readonly", BG_INPUT)], foreground=[("readonly", FG_PRIMARY)])

        style.configure(
            "TCheckbutton",
            background=BG_CARD,
            foreground=FG_PRIMARY,
            font=("Segoe UI", 9),
        )
        style.map("TCheckbutton", background=[("active", BG_CARD)])

        style.configure(
            "Blue.Horizontal.TProgressbar",
            troughcolor=BG_INPUT,
            background=ACCENT_BLUE,
            bordercolor=BORDER_SUBTLE,
            lightcolor=ACCENT_BLUE,
            darkcolor=ACCENT_BLUE_HOVER,
        )

    def _build_variables(self) -> None:
        hw = self.cfg.get("hardware", {})
        proto = self.cfg.get("protocol", {})
        ser_cfg = self.cfg.get("serial", {})

        self.var_port = tk.StringVar(value="AUTO (Auto-Detect ESP32)")
        self.var_baud = tk.StringVar(value=str(ser_cfg.get("baud_rate", 921600)))
        self.var_r_ref = tk.StringVar(value=str(hw.get("r_ref_ohm", 470.0)))
        self.var_r_ref_hint = tk.StringVar(value="Recommended for CR2032 / CR2025 coin cells (220 - 470 Ohm)")
        self.var_temp_c = tk.StringVar(value="")
        self.var_soc_pct = tk.StringVar(value="")

        self.var_ocv_s = tk.StringVar(value=str(proto.get("ocv_hold_sec", 2.0)))
        self.var_pulse_s = tk.StringVar(value=str(proto.get("pulse_duration_sec", 1.0)))
        self.var_relax_s = tk.StringVar(value=str(proto.get("relax_sec", 5.0)))
        self.var_rate_hz = tk.StringVar(value=str(proto.get("sample_rate_hz", 1000)))

        self.var_auto_switch = tk.BooleanVar(value=True)
        self.var_save_cfg = tk.BooleanVar(value=True)

        self.var_ocv_live = tk.StringVar(value="OCV Probe: Not checked")
        self.var_phase_status = tk.StringVar(value="Ready. Connect ESP32 or use Simulate / Analyze CSV.")
        self.var_progress = tk.DoubleVar(value=0.0)
        self.var_run_select = tk.StringVar(value="")
        self.var_run_meta = tk.StringVar(value="No run selected yet.")

        self.var_r_ref.trace_add("write", lambda *_: self._update_r_ref_hint())

    def _update_r_ref_hint(self) -> None:
        try:
            r = float(self.var_r_ref.get().strip())
            if r <= 0:
                self.var_r_ref_hint.set("Warning: R_ref must be positive (> 0 Ohm).")
            elif r < 90:
                self.var_r_ref_hint.set(f"High current (~{3.1 / r * 1000:.1f} mA @ 3.1V). Verify GPIO 25 <= 40 mA max!")
            elif r <= 180:
                self.var_r_ref_hint.set(f"Good for AA/AAA Alkaline or NiMH cells (~{1.5 / r * 1000:.1f} mA @ 1.5V).")
            elif r <= 560:
                self.var_r_ref_hint.set(f"Ideal for CR2032/CR2025 coin cells (~{3.1 / r * 1000:.1f} mA @ 3.1V).")
            else:
                self.var_r_ref_hint.set(f"Gentle pulse (~{3.1 / r * 1000:.1f} mA @ 3.1V); good for high-R cells.")
        except ValueError:
            self.var_r_ref_hint.set("Enter a numeric resistance in Ohms (e.g., 470).")

    # ------------------------------------------------------------------
    # UI Construction
    # ------------------------------------------------------------------
    def _build_layout(self) -> None:
        header = tk.Frame(self.root, bg=BG_APP, padx=18, pady=12)
        header.pack(fill="x")

        title_box = tk.Frame(header, bg=BG_APP)
        title_box.pack(side="left", fill="x", expand=True)
        ttk.Label(title_box, text="ESP32 Battery Pulse & EIS Parameterization", style="AppTitle.TLabel").pack(anchor="w")
        ttk.Label(
            title_box,
            text="Square-Wave Current Modulation (GPIO 25 + GPIO 34)  |  1-RC / 2-RC / EIS / DRT Analysis",
            style="AppSub.TLabel",
        ).pack(anchor="w", pady=(2, 0))

        self.lbl_conn_badge = tk.Label(
            header,
            text="● IDLE",
            bg=BG_CARD,
            fg=ACCENT_BLUE,
            font=("Segoe UI", 9, "bold"),
            padx=12,
            pady=6,
            relief="flat",
        )
        self.lbl_conn_badge.pack(side="right")

        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=16, pady=(0, 14))

        self.tab_control = tk.Frame(self.notebook, bg=BG_APP)
        self.tab_results = tk.Frame(self.notebook, bg=BG_APP)
        self.notebook.add(self.tab_control, text="  1. Control & Measure  ")
        self.notebook.add(self.tab_results, text="  2. Results & Plots  ")

        self._build_control_tab()
        self._build_results_tab()

    def _make_card(self, parent: tk.Widget, title: str) -> tk.Frame:
        outer = tk.Frame(parent, bg=BORDER_SUBTLE, padx=1, pady=1)
        inner = tk.Frame(outer, bg=BG_CARD, padx=14, pady=12)
        inner.pack(fill="both", expand=True)
        ttk.Label(inner, text=title, style="CardTitle.TLabel").pack(anchor="w", pady=(0, 8))
        return outer, inner

    def _make_entry(self, parent: tk.Widget, var: tk.StringVar, width: int = 14, font_size: int = 10) -> tk.Entry:
        return tk.Entry(
            parent,
            textvariable=var,
            width=width,
            bg=BG_INPUT,
            fg=FG_PRIMARY,
            insertbackground=FG_PRIMARY,
            relief="flat",
            highlightthickness=1,
            highlightbackground=BORDER_SUBTLE,
            highlightcolor=ACCENT_BLUE,
            font=("Consolas", font_size, "bold" if font_size >= 12 else "normal"),
        )

    def _make_btn(
        self,
        parent: tk.Widget,
        text: str,
        cmd: Any,
        bg: str = BORDER_SUBTLE,
        fg: str = FG_PRIMARY,
        font: Tuple[Any, ...] = ("Segoe UI", 9, "bold"),
        padx: int = 12,
        pady: int = 6,
    ) -> tk.Button:
        return tk.Button(
            parent,
            text=text,
            command=cmd,
            bg=bg,
            fg=fg,
            activebackground=ACCENT_BLUE_HOVER,
            activeforeground="#FFFFFF",
            relief="flat",
            cursor="hand2",
            font=font,
            padx=padx,
            pady=pady,
        )

    def _build_control_tab(self) -> None:
        container = tk.Frame(self.tab_control, bg=BG_APP, pady=10)
        container.pack(fill="both", expand=True)
        container.columnconfigure(0, weight=5, uniform="col")
        container.columnconfigure(1, weight=6, uniform="col")
        container.rowconfigure(0, weight=1)

        left_col = tk.Frame(container, bg=BG_APP)
        left_col.grid(row=0, column=0, sticky="nsew", padx=(0, 8))

        right_col = tk.Frame(container, bg=BG_APP)
        right_col.grid(row=0, column=1, sticky="nsew", padx=(8, 0))

        # --- Card 1: Hardware / COM Port ---
        c_hw_out, c_hw = self._make_card(left_col, "ESP32 Hardware Connection")
        c_hw_out.pack(fill="x", pady=(0, 10))

        row_port = tk.Frame(c_hw, bg=BG_CARD)
        row_port.pack(fill="x", pady=(0, 8))
        ttk.Label(row_port, text="Serial Port:").pack(side="left")
        self.cmb_port = ttk.Combobox(row_port, textvariable=self.var_port, state="readonly", width=34)
        self.cmb_port.pack(side="left", padx=8, fill="x", expand=True)
        self._make_btn(row_port, "Scan", lambda: self.scan_ports(silent=False)).pack(side="left")

        row_hw_btns = tk.Frame(c_hw, bg=BG_CARD)
        row_hw_btns.pack(fill="x")
        self._make_btn(row_hw_btns, "Check OCV / Ping", self.on_check_ocv, bg="#1D4ED8").pack(side="left", padx=(0, 8))
        self._make_btn(row_hw_btns, "Flash ESP32 Firmware", self.on_flash_firmware, bg="#334155").pack(side="left")
        ttk.Label(row_hw_btns, textvariable=self.var_ocv_live, style="Muted.TLabel").pack(side="right")

        # --- Card 2: Reference Resistor & Cell Metadata ---
        c_ref_out, c_ref = self._make_card(left_col, "Reference Resistor (R_ref) & Cell Conditions")
        c_ref_out.pack(fill="x", pady=(0, 10))

        row_rref = tk.Frame(c_ref, bg=BG_CARD)
        row_rref.pack(fill="x", pady=(0, 6))
        ttk.Label(row_rref, text="Reference Resistor R_ref (Ω):", font=("Segoe UI", 10, "bold")).pack(side="left")
        self.ent_r_ref = self._make_entry(row_rref, self.var_r_ref, width=12, font_size=13)
        self.ent_r_ref.pack(side="left", padx=10, ipady=3)
        ttk.Checkbutton(row_rref, text="Save as default", variable=self.var_save_cfg).pack(side="left")

        ttk.Label(c_ref, textvariable=self.var_r_ref_hint, style="Muted.TLabel").pack(anchor="w", pady=(0, 8))

        row_presets = tk.Frame(c_ref, bg=BG_CARD)
        row_presets.pack(fill="x", pady=(0, 10))
        ttk.Label(row_presets, text="Presets:", style="Muted.TLabel").pack(side="left", padx=(0, 6))
        for val, label in [("100", "100 Ω (AA)"), ("220", "220 Ω (Coin)"), ("330", "330 Ω"), ("470", "470 Ω (CR2032)"), ("990", "990 Ω")]:
            self._make_btn(
                row_presets,
                label,
                lambda v=val: self.var_r_ref.set(v),
                bg=BG_INPUT,
                fg=ACCENT_BLUE,
                font=("Segoe UI", 8, "bold"),
                padx=8,
                pady=3,
            ).pack(side="left", padx=3)

        row_meta = tk.Frame(c_ref, bg=BG_CARD)
        row_meta.pack(fill="x")
        ttk.Label(row_meta, text="Temperature (°C, opt):").pack(side="left")
        self._make_entry(row_meta, self.var_temp_c, width=8).pack(side="left", padx=(6, 16), ipady=2)
        ttk.Label(row_meta, text="SoC (%, opt):").pack(side="left")
        self._make_entry(row_meta, self.var_soc_pct, width=8).pack(side="left", padx=6, ipady=2)

        # --- Card 3: Pulse Protocol Timings ---
        c_proto_out, c_proto = self._make_card(left_col, "Square-Wave Pulse Protocol Timings")
        c_proto_out.pack(fill="x")

        grid_p = tk.Frame(c_proto, bg=BG_CARD)
        grid_p.pack(fill="x")
        specs = [
            ("High-Z OCV Hold (s):", self.var_ocv_s, 0, 0),
            ("Pulse Width (s):", self.var_pulse_s, 0, 2),
            ("Relaxation Recovery (s):", self.var_relax_s, 1, 0),
            ("Sample Rate (Hz):", self.var_rate_hz, 1, 2),
        ]
        for lbl, var, r, c in specs:
            ttk.Label(grid_p, text=lbl).grid(row=r, column=c, sticky="w", padx=(0, 6), pady=4)
            self._make_entry(grid_p, var, width=9).grid(row=r, column=c + 1, sticky="w", padx=(0, 18), pady=4, ipady=2)

        ttk.Label(
            c_proto,
            text="Timing Diagram: [Phase 0: High-Z OCV] -> [Phase 1: LOW Pulse via R_ref] -> [Phase 2: High-Z Relaxation]",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(6, 0))

        # --- Right Column Top: Execution & Progress ---
        c_run_out, c_run = self._make_card(right_col, "Execute Pulse & Parameter Extraction")
        c_run_out.pack(fill="x", pady=(0, 10))

        self.btn_run_pulse = self._make_btn(
            c_run,
            "▶  SEND PULSE & EXTRACT PARAMETERS",
            self.on_run_pulse,
            bg=ACCENT_GREEN,
            fg="#052E16",
            font=("Segoe UI", 12, "bold"),
            padx=16,
            pady=12,
        )
        self.btn_run_pulse.pack(fill="x", pady=(0, 10))

        row_alt = tk.Frame(c_run, bg=BG_CARD)
        row_alt.pack(fill="x", pady=(0, 10))
        self.btn_sim = self._make_btn(row_alt, "Simulate Pulse (No Hardware)", self.on_run_simulation, bg="#1D4ED8")
        self.btn_sim.pack(side="left", padx=(0, 8), fill="x", expand=True)
        self.btn_csv = self._make_btn(row_alt, "Analyze Existing CSV...", self.on_analyze_csv, bg="#334155")
        self.btn_csv.pack(side="left", fill="x", expand=True)

        ttk.Checkbutton(
            c_run,
            text="Automatically switch to 'Results & Plots' tab when finished",
            variable=self.var_auto_switch,
        ).pack(anchor="w", pady=(0, 8))

        ttk.Label(c_run, textvariable=self.var_phase_status, font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(0, 4))
        self.progress = ttk.Progressbar(
            c_run,
            variable=self.var_progress,
            maximum=100,
            mode="determinate",
            style="Blue.Horizontal.TProgressbar",
        )
        self.progress.pack(fill="x")

        # --- Right Column Bottom: Live Console Log ---
        c_log_out, c_log = self._make_card(right_col, "Live Acquisition & Extraction Log")
        c_log_out.pack(fill="both", expand=True)

        self.txt_log = scrolledtext.ScrolledText(
            c_log,
            bg=BG_INPUT,
            fg="#CBD5E1",
            insertbackground=FG_PRIMARY,
            relief="flat",
            font=("Consolas", 9),
            wrap="word",
            height=14,
        )
        self.txt_log.pack(fill="both", expand=True)
        self.txt_log.configure(state="disabled")

    def _build_results_tab(self) -> None:
        container = tk.Frame(self.tab_results, bg=BG_APP, pady=10)
        container.pack(fill="both", expand=True)

        # Top toolbar
        top_out, top_bar = self._make_card(container, "Measurement Run Selector & Exports")
        top_out.pack(fill="x", pady=(0, 10))

        row_top = tk.Frame(top_bar, bg=BG_CARD)
        row_top.pack(fill="x")
        ttk.Label(row_top, text="Saved Run:").pack(side="left")
        self.cmb_runs = ttk.Combobox(row_top, textvariable=self.var_run_select, state="readonly", width=36)
        self.cmb_runs.pack(side="left", padx=8)
        self.cmb_runs.bind("<<ComboboxSelected>>", lambda _e: self.on_select_run())

        self._make_btn(row_top, "Refresh", lambda: self.refresh_runs_list(select_latest=False)).pack(side="left", padx=(0, 8))
        self._make_btn(row_top, "Open Full-Res Plot", self.on_open_plot, bg="#1D4ED8").pack(side="left", padx=(0, 6))
        self._make_btn(row_top, "Open Run Folder", self.on_open_folder).pack(side="left", padx=(0, 6))
        self._make_btn(row_top, "Open JSON", self.on_open_json).pack(side="left", padx=(0, 6))
        self._make_btn(row_top, "Analyze CSV...", self.on_analyze_csv).pack(side="right")

        ttk.Label(top_bar, textvariable=self.var_run_meta, style="Muted.TLabel").pack(anchor="w", pady=(6, 0))

        # Metric cards grid (2 rows x 4 columns)
        metrics_frame = tk.Frame(container, bg=BG_APP)
        metrics_frame.pack(fill="x", pady=(0, 10))
        for col_idx in range(4):
            metrics_frame.columnconfigure(col_idx, weight=1, uniform="mcol")

        self.metric_vars: Dict[str, Tuple[tk.StringVar, tk.StringVar]] = {}
        card_defs = [
            ("v_ocv", "OPEN-CIRCUIT VOLTAGE (V_OCV)"),
            ("i_pulse", "MEAN PULSE CURRENT (I_pulse)"),
            ("r_s", "OHMIC RESISTANCE (R_s)"),
            ("r_ct", "CHARGE-TRANSFER (R_ct)"),
            ("c_dl", "DOUBLE-LAYER CAP (C_dl)"),
            ("two_rc", "2-RC RELAXATION (τ₁ / τ₂)"),
            ("eis", "EIS SYNTHETIC FIT (R₀ / R₁)"),
            ("energy", "PULSE CHARGE & POWER"),
        ]
        for idx, (key, title) in enumerate(card_defs):
            r, c = divmod(idx, 4)
            outer = tk.Frame(metrics_frame, bg=BORDER_SUBTLE, padx=1, pady=1)
            outer.grid(row=r, column=c, sticky="nsew", padx=4, pady=4)
            box = tk.Frame(outer, bg=BG_INPUT, padx=10, pady=8)
            box.pack(fill="both", expand=True)
            v_main = tk.StringVar(value="--")
            v_sub = tk.StringVar(value="--")
            self.metric_vars[key] = (v_main, v_sub)
            ttk.Label(box, text=title, style="MetricTitle.TLabel").pack(anchor="w")
            ttk.Label(box, textvariable=v_main, style="MetricVal.TLabel").pack(anchor="w", pady=(2, 0))
            ttk.Label(box, textvariable=v_sub, style="MetricSub.TLabel").pack(anchor="w", pady=(1, 0))

        # Embedded Plot Preview Card
        plot_out, plot_card = self._make_card(container, "4-Panel Diagnostic Plot Preview (Click Image to Open Full Resolution)")
        plot_out.pack(fill="both", expand=True)

        self.lbl_plot = tk.Label(
            plot_card,
            text="Run a pulse measurement or simulation to display the 4-panel voltage, 2-RC fit, EIS Nyquist, and DRT plots.",
            bg=BG_INPUT,
            fg=FG_MUTED,
            font=("Segoe UI", 10),
            cursor="hand2",
        )
        self.lbl_plot.pack(fill="both", expand=True)
        self.lbl_plot.bind("<Button-1>", lambda _e: self.on_open_plot())
        self.lbl_plot.bind("<Configure>", lambda _e: self._render_plot_preview())

    # ------------------------------------------------------------------
    # Logging & Thread-Safe UI Queue
    # ------------------------------------------------------------------
    def log(self, msg: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        self.ui_queue.put(("log", f"[{ts}] {msg}"))

    def _append_log(self, line: str) -> None:
        self.txt_log.configure(state="normal")
        self.txt_log.insert("end", line + "\n")
        self.txt_log.see("end")
        self.txt_log.configure(state="disabled")

    def _set_busy(self, busy: bool, badge_text: str = "● BUSY", badge_color: str = ACCENT_AMBER) -> None:
        self.is_busy = busy
        state = "disabled" if busy else "normal"
        self.btn_run_pulse.configure(state=state)
        self.btn_sim.configure(state=state)
        self.btn_csv.configure(state=state)
        if busy:
            self.lbl_conn_badge.configure(text=badge_text, fg=badge_color)
        else:
            self.lbl_conn_badge.configure(text="● READY", fg=ACCENT_GREEN)

    def _poll_ui_queue(self) -> None:
        try:
            while True:
                kind, payload = self.ui_queue.get_nowait()
                if kind == "log":
                    self._append_log(str(payload))
                elif kind == "status":
                    msg, pct = payload
                    self.var_phase_status.set(msg)
                    if pct is not None:
                        self.var_progress.set(float(pct))
                elif kind == "ocv":
                    self.var_ocv_live.set(str(payload))
                elif kind == "done_run":
                    self._on_run_completed(payload)
                elif kind == "error":
                    self._set_busy(False)
                    self.var_phase_status.set(f"Error: {payload}")
                    self.lbl_conn_badge.configure(text="● ERROR", fg=ACCENT_RED)
                    messagebox.showerror("Operation Failed", str(payload))
                elif kind == "flash_done":
                    self._set_busy(False)
                    self.var_phase_status.set(str(payload))
        except queue.Empty:
            pass
        self.root.after(80, self._poll_ui_queue)

    # ------------------------------------------------------------------
    # Input Parsing & Validation
    # ------------------------------------------------------------------
    def _parse_inputs(self) -> Tuple[float, Optional[float], Optional[float], float, float, float, int]:
        try:
            r_ref = float(self.var_r_ref.get().strip())
        except ValueError as exc:
            raise ValueError("Reference resistor R_ref must be a valid number (Ohms).") from exc
        if r_ref <= 0:
            raise ValueError("Reference resistor R_ref must be greater than 0 Ohms.")

        temp_str = self.var_temp_c.get().strip()
        temp_c = float(temp_str) if temp_str else None

        soc_str = self.var_soc_pct.get().strip()
        soc_pct = float(soc_str) if soc_str else None

        ocv_s = float(self.var_ocv_s.get().strip())
        pulse_s = float(self.var_pulse_s.get().strip())
        relax_s = float(self.var_relax_s.get().strip())
        rate_hz = int(float(self.var_rate_hz.get().strip()))
        if min(ocv_s, pulse_s, relax_s) <= 0 or rate_hz <= 0:
            raise ValueError("Protocol durations and sample rate must all be positive numbers.")

        return r_ref, temp_c, soc_pct, ocv_s, pulse_s, relax_s, rate_hz

    def _resolve_selected_port(self) -> str:
        sel = self.var_port.get()
        val = self.port_map.get(sel, "AUTO")
        if val == "AUTO":
            detected = auto_detect_esp32_port()
            if not detected:
                raise RuntimeError(
                    "No ESP32 serial port detected automatically. Connect the ESP32 via USB, click 'Scan', "
                    "or select the COM port manually."
                )
            return detected
        return val

    # ------------------------------------------------------------------
    # Hardware Actions
    # ------------------------------------------------------------------
    def scan_ports(self, silent: bool = False) -> None:
        ports = list(serial.tools.list_ports.comports())
        auto_port = auto_detect_esp32_port()
        self.port_map = {"AUTO (Auto-Detect ESP32)": "AUTO"}
        display_items = ["AUTO (Auto-Detect ESP32)"]
        for p in ports:
            label = f"{p.device} - {p.description}"
            self.port_map[label] = p.device
            display_items.append(label)

        self.cmb_port["values"] = display_items
        if self.var_port.get() not in self.port_map:
            self.var_port.set(display_items[0])

        if not silent:
            if auto_port:
                self.log(f"Found {len(ports)} serial port(s). Auto-detect candidate: {auto_port}")
            else:
                self.log(f"Found {len(ports)} serial port(s). No USB-UART bridge auto-matched; choose manually if needed.")

    def on_check_ocv(self) -> None:
        if self.is_busy:
            return
        self._set_busy(True, "● PROBING OCV", ACCENT_BLUE)
        self.ui_queue.put(("status", ("Probing ESP32 PING & live OCV...", 15)))
        threading.Thread(target=self._worker_check_ocv, daemon=True).start()

    def _worker_check_ocv(self) -> None:
        try:
            port = self._resolve_selected_port()
            baud = int(self.var_baud.get().strip())
            self.log(f"Opening {port} @ {baud} baud for PING/OCV check...")
            with serial.Serial(port, baud, timeout=2.0) as ser:
                time.sleep(0.15)
                ser.reset_input_buffer()
                ser.write(b"PING\n")
                ping_resp = ""
                for _ in range(10):
                    line = ser.readline().decode("utf-8", errors="ignore").strip()
                    if line:
                        self.log(f"ESP32: {line}")
                        if line.startswith("# READY"):
                            ping_resp = line
                            break
                ser.write(b"OCV\n")
                ocv_mv: Optional[float] = None
                for _ in range(10):
                    line = ser.readline().decode("utf-8", errors="ignore").strip()
                    if line.startswith("OCV_MV:"):
                        ocv_mv = float(line.split(":", 1)[1].strip())
                        break
            if ocv_mv is not None:
                badge = f"OCV: {ocv_mv / 1000.0:.3f} V ({ocv_mv:.0f} mV) on {port}"
                self.ui_queue.put(("ocv", badge))
                self.log(f"Live High-Z Open-Circuit Voltage = {ocv_mv:.1f} mV ({ocv_mv / 1000.0:.3f} V)")
            elif ping_resp:
                self.ui_queue.put(("ocv", f"Ready on {port}"))
            else:
                self.ui_queue.put(("ocv", f"No firmware reply on {port}"))
                self.log("No # READY reply. Verify ESP32 firmware is flashed.")
            self.ui_queue.put(("status", ("OCV probe finished.", 0)))
            self.ui_queue.put(("flash_done", "OCV probe finished."))
        except Exception as exc:
            self.ui_queue.put(("error", str(exc)))

    def on_flash_firmware(self) -> None:
        if self.is_busy:
            return
        if not messagebox.askyesno(
            "Flash ESP32 Firmware",
            "Build and upload firmware/src/main.cpp to the connected ESP32 via PlatformIO?",
        ):
            return
        self._set_busy(True, "● FLASHING", ACCENT_AMBER)
        self.ui_queue.put(("status", ("Building & flashing ESP32 firmware with PlatformIO...", 25)))
        threading.Thread(target=self._worker_flash_firmware, daemon=True).start()

    def _worker_flash_firmware(self) -> None:
        try:
            pio_exe = PROJECT_ROOT / ".venv" / "Scripts" / "pio.exe"
            cmd = [str(pio_exe) if pio_exe.exists() else "pio", "run", "-d", str(PROJECT_ROOT / "firmware"), "-t", "upload"]
            sel = self.port_map.get(self.var_port.get(), "AUTO")
            if sel != "AUTO":
                cmd.extend(["--upload-port", sel])
            self.log("Running: " + " ".join(cmd))
            proc = subprocess.Popen(
                cmd,
                cwd=str(PROJECT_ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            assert proc.stdout is not None
            for line in proc.stdout:
                clean = line.rstrip()
                if clean:
                    self.log(clean)
            rc = proc.wait()
            if rc != 0:
                raise RuntimeError(f"PlatformIO upload exited with code {rc}. Check USB cable / BOOT button.")
            self.log("Firmware flashed successfully!")
            self.ui_queue.put(("status", ("ESP32 firmware flashed successfully.", 100)))
            self.ui_queue.put(("flash_done", "ESP32 firmware flashed successfully."))
        except Exception as exc:
            self.ui_queue.put(("error", str(exc)))

    # ------------------------------------------------------------------
    # Pulse Execution, Simulation & CSV Analysis
    # ------------------------------------------------------------------
    def on_run_pulse(self) -> None:
        if self.is_busy:
            return
        try:
            params = self._parse_inputs()
        except ValueError as exc:
            messagebox.showwarning("Invalid Input", str(exc))
            return

        r_ref = params[0]
        if self.var_save_cfg.get():
            save_r_ref_to_config(r_ref)

        self._set_busy(True, "● PULSING", ACCENT_AMBER)
        self.ui_queue.put(("status", ("Connecting to ESP32...", 5)))
        threading.Thread(target=self._worker_run_pulse, args=params, daemon=True).start()

    def _worker_run_pulse(
        self,
        r_ref: float,
        temp_c: Optional[float],
        soc_pct: Optional[float],
        ocv_s: float,
        pulse_s: float,
        relax_s: float,
        rate_hz: int,
    ) -> None:
        try:
            port = self._resolve_selected_port()
            baud = int(self.var_baud.get().strip())
            ocv_ms = int(round(ocv_s * 1000))
            pulse_ms = int(round(pulse_s * 1000))
            relax_ms = int(round(relax_s * 1000))
            period_us = max(100, int(round(1_000_000 / rate_hz)))
            total_s = ocv_s + pulse_s + relax_s

            self.log(
                f"Starting ESP32 pulse on {port} (R_ref={r_ref:.2f} Ohm, "
                f"OCV={ocv_s:.1f}s -> Pulse={pulse_s:.1f}s -> Relax={relax_s:.1f}s @ {rate_hz} Hz)"
            )

            stop_timer = threading.Event()

            def _progress_ticker() -> None:
                t0 = time.monotonic()
                while not stop_timer.is_set():
                    elapsed = time.monotonic() - t0
                    if elapsed < ocv_s:
                        phase_msg = f"Phase 0/2: Measuring High-Z OCV ({elapsed:.1f}/{ocv_s:.1f}s)..."
                    elif elapsed < ocv_s + pulse_s:
                        phase_msg = f"Phase 1/2: Drawing Square Current Pulse via R_ref={r_ref:.1f}Ω ({elapsed - ocv_s:.1f}/{pulse_s:.1f}s)..."
                    elif elapsed < total_s:
                        phase_msg = f"Phase 2/2: Recording Relaxation Recovery ({elapsed - ocv_s - pulse_s:.1f}/{relax_s:.1f}s)..."
                    else:
                        phase_msg = "Streaming telemetry from ESP32..."
                    pct = min(82.0, 10.0 + (elapsed / max(total_s, 0.1)) * 72.0)
                    self.ui_queue.put(("status", (phase_msg, pct)))
                    time.sleep(0.15)

            ticker = threading.Thread(target=_progress_ticker, daemon=True)
            ticker.start()
            try:
                df, hw_meta = trigger_and_acquire_esp32(
                    port=port,
                    baud_rate=baud,
                    ocv_ms=ocv_ms,
                    pulse_ms=pulse_ms,
                    relax_ms=relax_ms,
                    sample_period_us=period_us,
                    r_ref_ohm=r_ref,
                )
            finally:
                stop_timer.set()
                ticker.join(timeout=0.5)

            self.log(f"Acquired {len(df)} samples from ESP32. Running 1-RC / 2-RC / EIS / DRT extraction...")
            self.ui_queue.put(("status", ("Extracting Randles, 2-RC, EIS & DRT parameters...", 88)))

            extra_meta: Dict[str, Any] = {
                "source": "esp32_gui",
                "port": port,
                "r_ref_ohm": r_ref,
                **hw_meta,
            }
            if temp_c is not None:
                extra_meta["temperature_c"] = temp_c
            if soc_pct is not None:
                extra_meta["soc_pct"] = soc_pct

            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            run_dir = RUNS_DIR / f"{stamp}_r{int(round(r_ref))}"
            params_obj, curves = extract_randles_parameters(df, r_ref_ohm=r_ref, fit_eis=True)
            saved = save_all_run_outputs(params_obj, curves, run_dir=run_dir, stem="pulse", extra_meta=extra_meta)

            self.log(
                f"Done! V_OCV={params_obj.v_ocv_v:.4f}V, R_s={params_obj.r_s_ohm:.3f}Ω, "
                f"R_ct={params_obj.r_ct_ohm:.3f}Ω, C_dl={params_obj.c_dl_farad * 1000:.3f}mF"
            )
            self.ui_queue.put(("status", (f"Measurement complete -> {run_dir.name}", 100)))
            self.ui_queue.put(("done_run", {"run_dir": run_dir, "params": params_obj, "saved": saved, "extra": extra_meta}))
        except Exception as exc:
            self.ui_queue.put(("error", str(exc)))

    def on_run_simulation(self) -> None:
        if self.is_busy:
            return
        try:
            r_ref, temp_c, soc_pct, *_ = self._parse_inputs()
        except ValueError as exc:
            messagebox.showwarning("Invalid Input", str(exc))
            return

        self._set_busy(True, "● SIMULATING", ACCENT_BLUE)
        self.ui_queue.put(("status", ("Generating synthetic battery pulse & running extraction...", 35)))
        threading.Thread(target=self._worker_run_simulation, args=(r_ref, temp_c, soc_pct), daemon=True).start()

    def _worker_run_simulation(self, r_ref: float, temp_c: Optional[float], soc_pct: Optional[float]) -> None:
        try:
            df = generate_synthetic_pulse_df(r_ref_ohm=r_ref)
            self.log(f"Generated synthetic Randles pulse ({len(df)} samples) with R_ref={r_ref:.2f} Ohm.")
            self.ui_queue.put(("status", ("Extracting 1-RC, 2-RC, EIS & DRT parameters...", 75)))

            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            run_dir = RUNS_DIR / f"{stamp}_sim_r{int(round(r_ref))}"
            extra_meta: Dict[str, Any] = {"source": "gui_simulation", "r_ref_ohm": r_ref}
            if temp_c is not None:
                extra_meta["temperature_c"] = temp_c
            if soc_pct is not None:
                extra_meta["soc_pct"] = soc_pct

            params_obj, curves = extract_randles_parameters(df, r_ref_ohm=r_ref, fit_eis=True)
            saved = save_all_run_outputs(params_obj, curves, run_dir=run_dir, stem="pulse", extra_meta=extra_meta)

            self.log(
                f"Simulation saved to {run_dir}. R_s={params_obj.r_s_ohm:.3f}Ω, R_ct={params_obj.r_ct_ohm:.3f}Ω"
            )
            self.ui_queue.put(("status", (f"Simulation complete -> {run_dir.name}", 100)))
            self.ui_queue.put(("done_run", {"run_dir": run_dir, "params": params_obj, "saved": saved, "extra": extra_meta}))
        except Exception as exc:
            self.ui_queue.put(("error", str(exc)))

    def on_analyze_csv(self) -> None:
        if self.is_busy:
            return
        try:
            r_ref, temp_c, soc_pct, *_ = self._parse_inputs()
        except ValueError as exc:
            messagebox.showwarning("Invalid Input", str(exc))
            return

        csv_path = filedialog.askopenfilename(
            title="Select Battery Pulse CSV",
            initialdir=str(RUNS_DIR if RUNS_DIR.exists() else PROJECT_ROOT),
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if not csv_path:
            return

        self._set_busy(True, "● ANALYZING CSV", ACCENT_BLUE)
        self.ui_queue.put(("status", (f"Analyzing {Path(csv_path).name}...", 40)))
        threading.Thread(
            target=self._worker_analyze_csv, args=(Path(csv_path), r_ref, temp_c, soc_pct), daemon=True
        ).start()

    def _worker_analyze_csv(
        self, csv_path: Path, r_ref: float, temp_c: Optional[float], soc_pct: Optional[float]
    ) -> None:
        try:
            df = pd.read_csv(csv_path, comment="#")
            self.log(f"Loaded CSV '{csv_path.name}' ({len(df)} rows). Re-extracting with R_ref={r_ref:.2f} Ohm...")
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            run_dir = RUNS_DIR / f"{stamp}_csv_r{int(round(r_ref))}"
            extra_meta: Dict[str, Any] = {"source": f"csv:{csv_path.name}", "r_ref_ohm": r_ref}
            if temp_c is not None:
                extra_meta["temperature_c"] = temp_c
            if soc_pct is not None:
                extra_meta["soc_pct"] = soc_pct

            params_obj, curves = extract_randles_parameters(df, r_ref_ohm=r_ref, fit_eis=True)
            saved = save_all_run_outputs(params_obj, curves, run_dir=run_dir, stem="pulse", extra_meta=extra_meta)
            self.log(f"CSV analysis saved -> {run_dir}")
            self.ui_queue.put(("status", (f"Analysis complete -> {run_dir.name}", 100)))
            self.ui_queue.put(("done_run", {"run_dir": run_dir, "params": params_obj, "saved": saved, "extra": extra_meta}))
        except Exception as exc:
            self.ui_queue.put(("error", str(exc)))

    # ------------------------------------------------------------------
    # Results Loading, Display & Navigation
    # ------------------------------------------------------------------
    def _on_run_completed(self, result: Dict[str, Any]) -> None:
        self._set_busy(False)
        run_dir: Path = result["run_dir"]
        self.refresh_runs_list(select_run_dir=run_dir)
        self._load_run_into_results(run_dir)
        if self.var_auto_switch.get():
            self.notebook.select(self.tab_results)

    def refresh_runs_list(self, select_latest: bool = False, select_run_dir: Optional[Path] = None) -> None:
        self.run_map.clear()
        if not RUNS_DIR.exists():
            self.cmb_runs["values"] = []
            return

        dirs = sorted(
            [p for p in RUNS_DIR.iterdir() if p.is_dir() and any(p.glob("*_params.json"))],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        labels: List[str] = []
        for d in dirs:
            labels.append(d.name)
            self.run_map[d.name] = d

        self.cmb_runs["values"] = labels
        if not labels:
            self.var_run_select.set("")
            return

        if select_run_dir and select_run_dir.name in self.run_map:
            self.var_run_select.set(select_run_dir.name)
            self._load_run_into_results(select_run_dir)
        elif select_latest or self.var_run_select.get() not in self.run_map:
            self.var_run_select.set(labels[0])
            self._load_run_into_results(self.run_map[labels[0]])

    def on_select_run(self) -> None:
        name = self.var_run_select.get()
        if name in self.run_map:
            self._load_run_into_results(self.run_map[name])

    def _load_run_into_results(self, run_dir: Path) -> None:
        json_files = sorted(run_dir.glob("*_params.json"))
        plot_files = sorted(run_dir.glob("*_plot.png"))
        if not json_files:
            return

        json_path = json_files[0]
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as exc:
            self.log(f"Could not read {json_path.name}: {exc}")
            return

        self.current_run_dir = run_dir
        self.current_json_path = json_path
        self.current_plot_path = plot_files[0] if plot_files else None

        def _f(key: str, default: float = 0.0) -> float:
            val = data.get(key)
            return float(val) if val is not None else default

        self.metric_vars["v_ocv"][0].set(f"{_f('v_ocv_v'):.4f} V")
        self.metric_vars["v_ocv"][1].set(f"Min: {_f('v_min_pulse_v'):.4f} V  |  ΔV: {_f('max_voltage_drop_mv'):.1f} mV")

        self.metric_vars["i_pulse"][0].set(f"{_f('i_pulse_mean_ma'):.2f} mA")
        self.metric_vars["i_pulse"][1].set(f"R_ref used: {_f('r_ref_ohm'):.2f} Ω")

        self.metric_vars["r_s"][0].set(f"{_f('r_s_ohm'):.3f} Ω")
        self.metric_vars["r_s"][1].set(f"R_DC Total: {_f('r_dc_total_ohm'):.3f} Ω")

        self.metric_vars["r_ct"][0].set(f"{_f('r_ct_ohm'):.3f} Ω")
        self.metric_vars["r_ct"][1].set(f"Relax R_ct: {_f('r_ct_relax_ohm'):.3f} Ω")

        self.metric_vars["c_dl"][0].set(f"{_f('c_dl_farad') * 1000.0:.3f} mF")
        self.metric_vars["c_dl"][1].set(f"τ = {_f('tau_sec'):.4f} s  |  Relax τ: {_f('tau_relax_sec'):.4f} s")

        self.metric_vars["two_rc"][0].set(f"{_f('tau1_fast_sec') * 1000:.1f} ms / {_f('tau2_slow_sec'):.3f} s")
        self.metric_vars["two_rc"][1].set(
            f"R1={_f('r1_fast_ohm'):.2f}Ω  R2={_f('r2_slow_ohm'):.2f}Ω  R²={_f('r_squared_2rc_relax'):.4f}"
        )

        eis_r0 = data.get("eis_r0_ohm")
        eis_r1 = data.get("eis_r1_ohm")
        if eis_r0 is not None and eis_r1 is not None:
            self.metric_vars["eis"][0].set(f"{float(eis_r0):.2f} / {float(eis_r1):.2f} Ω")
            self.metric_vars["eis"][1].set(
                f"Min Phase: {_f('eis_min_phase_deg'):.1f}° @ {_f('eis_peak_phase_freq_hz'):.2f} Hz"
            )
        else:
            self.metric_vars["eis"][0].set("N/A")
            self.metric_vars["eis"][1].set(f"Harmonics: {int(_f('eis_harmonic_count'))}")

        self.metric_vars["energy"][0].set(f"{_f('pulse_charge_mc'):.2f} mC")
        self.metric_vars["energy"][1].set(
            f"E: {_f('pulse_energy_mj'):.2f} mJ  |  P_max: {_f('max_theoretical_power_mw'):.1f} mW"
        )

        src = data.get("source", "measurement")
        temp = data.get("temperature_c")
        extra_bits = [f"Folder: {run_dir.name}", f"Source: {src}"]
        if temp is not None:
            extra_bits.append(f"Temp: {temp} °C")
        drt1 = data.get("drt_peak1_tau_sec")
        drt2 = data.get("drt_peak2_tau_sec")
        if drt1 is not None and drt2 is not None:
            extra_bits.append(f"DRT Peaks: {float(drt1):.4f}s, {float(drt2):.4f}s")
        self.var_run_meta.set("  |  ".join(extra_bits))

        if self.current_plot_path and self.current_plot_path.exists():
            self._pil_image = Image.open(self.current_plot_path)
            self._render_plot_preview()
        else:
            self._pil_image = None
            self.lbl_plot.configure(image="", text="No *_plot.png found in this run folder.")

    def _render_plot_preview(self) -> None:
        if self._pil_image is None:
            return
        w = max(self.lbl_plot.winfo_width(), 600)
        h = max(self.lbl_plot.winfo_height(), 320)
        img_w, img_h = self._pil_image.size
        scale = min(w / img_w, h / img_h)
        new_size = (max(1, int(img_w * scale)), max(1, int(img_h * scale)))
        resized = self._pil_image.resize(new_size, Image.Resampling.LANCZOS)
        self._tk_image = ImageTk.PhotoImage(resized)
        self.lbl_plot.configure(image=self._tk_image, text="")

    def on_open_plot(self) -> None:
        if self.current_plot_path and self.current_plot_path.exists():
            os.startfile(str(self.current_plot_path))  # type: ignore[attr-defined]
        else:
            messagebox.showinfo("No Plot", "No diagnostic plot PNG is available for the selected run.")

    def on_open_folder(self) -> None:
        target = self.current_run_dir if self.current_run_dir and self.current_run_dir.exists() else RUNS_DIR
        target.mkdir(parents=True, exist_ok=True)
        os.startfile(str(target))  # type: ignore[attr-defined]

    def on_open_json(self) -> None:
        if self.current_json_path and self.current_json_path.exists():
            os.startfile(str(self.current_json_path))  # type: ignore[attr-defined]
        else:
            messagebox.showinfo("No JSON", "No parameter JSON file is available for the selected run.")


def main() -> None:
    parser = argparse.ArgumentParser(description="ESP32 Battery Pulse & EIS Parameterization Desktop GUI")
    parser.add_argument(
        "--syntax-check",
        action="store_true",
        help="Instantiate the GUI headlessly to verify widgets, config, and run history, then exit 0.",
    )
    args = parser.parse_args()

    root = tk.Tk()
    if args.syntax_check:
        root.withdraw()
    app = BatteryPulseGUI(root)
    if args.syntax_check:
        root.update_idletasks()
        assert len(app.metric_vars) == 8
        print("GUI_OK")
        root.destroy()
        sys.exit(0)
    root.mainloop()


if __name__ == "__main__":
    main()
