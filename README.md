# ESP32 Battery Pulse Parameterization & EIS Suite

A minimalist, hardware-efficient electrochemical parameterization and **Electrochemical Impedance Spectroscopy (EIS)** platform for rechargeable and primary batteries. Using only **1x ESP32**, **1x Reference Resistor ($R_{\text{ref}}$)**, and a **3-wire breadboard topology** (no external MOSFETs or DC-blocking capacitors required), this system applies controlled square-wave current pulses via tri-state GPIO load modulation and extracts time-domain and frequency-domain equivalent circuit parameters.

---

## Table of Contents
1. [Measurement Principle & Architecture](#1-measurement-principle--architecture)
2. [Hardware Setup & 3-Wire Breadboard Wiring](#2-hardware-setup--3-wire-breadboard-wiring)
3. [Download & Installation Instructions](#3-download--installation-instructions)
4. [How to Use the System](#4-how-to-use-the-system)
5. [Generated Output Files](#5-generated-output-files)
6. [Complete Parameter Reference Guide (What Each Parameter Measures)](#6-complete-parameter-reference-guide-what-each-parameter-measures)
7. [Repository Structure](#7-repository-structure)

---

## 1. Measurement Principle & Architecture

Instead of attaching external surface temperature sensors (which suffer from thermal lag) or using bulky laboratory potentiostats, this platform measures the battery directly across its `(+)` and `(-)` terminals using **Tri-State GPIO Load Modulation**:

```text
1) Applied Current Load I(t) via ESP32 GPIO 25 (Tri-State Switch):

Current (mA)
  ^
  |          ┌──────────┐   <-- Phase 1 (1s): GPIO 25 = OUTPUT LOW (0V)
  |          │          │       Controlled current I_pulse = V_bat / R_ref flows
0 └──────────┘          └─────────────────────────> Time (s)
  0         2 s        3 s                       8 s
  (Phase 0: High-Z)    (Phase 2: High-Z Relaxation)


2) Measured Battery Terminal Voltage V(t) via ESP32 GPIO 34 (12-bit ADC):

Voltage (V)
  ^
  |──────────┐                   _ - - ───────────
  |          │  <-- Instantaneous Ohmic Drop (R_s) -> Indicates Core Temperature
  |          │               _ -
  |          \             /
  |           \__________/  <-- Exponential RC Decay & Recovery (R_ct, C_dl, tau)
  +-----------------------------------------------> Time (s)
  0         2 s        3 s                       8 s
```

- **Phase 0 ($0 - 2\text{ s}$, Open-Circuit / High-Z):** `GPIO 25` is configured as `INPUT` (High Impedance). Zero current flows ($I = 0$). `GPIO 34` measures the resting Open-Circuit Voltage ($V_{\text{OCV}}$).
- **Phase 1 ($2 - 3\text{ s}$, Controlled Square Pulse):** `GPIO 25` switches to `OUTPUT LOW` ($0\text{ V}$). Current flows from `Battery (+)` through $R_{\text{ref}}$ into `GPIO 25`. The firmware executes a **$40\text{ kHz}$ microsecond burst capture** during the first $5\text{ ms}$ to capture the instantaneous Ohmic drop ($\Delta V_0 \to R_s$), followed by $1\text{ kHz}$ sampling in RAM (zero UART jitter).
- **Phase 2 ($3 - 8\text{ s}$, High-Z Relaxation):** `GPIO 25` returns to `INPUT` (High-Z). Current is cut off instantaneously, and the multi-exponential voltage recovery curve is recorded to extract fast interfacial ($\tau_1$) and slow diffusive ($\tau_2$) relaxation dynamics.

---

## 2. Hardware Setup & 3-Wire Breadboard Wiring

### Required Components
- **1x ESP32 Development Board** (`ESP32 Dev Module`)
- **1x Reference Resistor ($R_{\text{ref}}$):** Typically **$100\,\Omega - 1\,\text{k}\Omega$** (e.g., $330\,\Omega$, $470\,\Omega$, or $990\,\Omega$ depending on cell internal resistance)
- **1x Breadboard & 3x Jumper Wires**
- **1x Battery Under Test (DUT):** Open-circuit voltage **$V_{\text{OCV}} \le 3.3\text{ V}$** for direct connection

### Breadboard Connection Table

```text
[ESP32 GPIO 25] ──> (Row 1) ── [ R_ref ] ── (Row 10) <── Connect BOTH [ESP32 GPIO 34] and [Battery (+)] here!
                                                         Connect [Battery (-)] directly to [ESP32 GND]
```

| Line | From | Through | To | Purpose |
| :--- | :--- | :--- | :--- | :--- |
| **1. Common Ground** | `Battery (-)` | Direct Wire | **`ESP32 GND`** | Common voltage reference |
| **2. Pulse Line** | **`ESP32 GPIO 25`** | **$R_{\text{ref}}$** | `Battery (+)` (Row 10) | Switches between `High-Z` (open) and `0V` (load sink) |
| **3. Sense Line** | `Battery (+)` (Row 10) | Direct Wire | **`ESP32 GPIO 34`** (`ADC1_CH6`) | Measures terminal voltage directly on the battery side of $R_{\text{ref}}$ |
| **4. Oscilloscope *(Optional)*** | Scope CH1 Probe | Direct | Tip $\to$ `Battery (+)`, Clip $\to$ `GND` | High-speed hybrid verification via `PyVISA` |

> **⚠️ Voltage Safety Warning:** ESP32 pins are rated for a maximum of **$3.3\text{ V}$**. Only connect cells with **$V_{\text{OCV}} \le 3.3\text{ V}$** (such as $1.5\text{ V}$ AA/AAA alkaline, $1.2\text{ V}$ NiMH, $3.0\text{ V}$ CR2032 coin cells, or $3.2\text{ V}$ LiFePO4 cells) directly to `GPIO 25` and `GPIO 34`. Do **not** connect $>3.3\text{ V}$ batteries ($3.7\text{ V}-4.2\text{ V}$ Li-ion or $9\text{ V}$ blocks) directly without a protective voltage divider and switching transistor.

---

## 3. Download & Installation Instructions

### Prerequisites
- **Python 3.10+** installed and added to `PATH`
- **Git** installed

### Option A: One-Click Automated Setup (Windows PowerShell)
Clone the repository and run the setup script, which creates the virtual environment, installs all Python scientific packages (`scipy`, `numpy`, `impedance`, `pybop`, `scikit-learn`, `pyserial`, `pyvisa`, `platformio`), and compiles the ESP32 firmware:

```powershell
git clone https://github.com/burakmentesoglu/ESP32-Battery-EIS-Parameterization.git
cd ESP32-Battery-EIS-Parameterization
.\setup_env.ps1
```

### Option B: Manual Step-by-Step Installation (Windows / Linux / macOS)

```bash
# 1. Clone the repository
git clone https://github.com/burakmentesoglu/ESP32-Battery-EIS-Parameterization.git
cd ESP32-Battery-EIS-Parameterization

# 2. Create and activate a Python virtual environment
python -m venv .venv

# Windows (PowerShell):
.\.venv\Scripts\Activate.ps1
# Linux / macOS:
# source .venv/bin/activate

# 3. Install required Python libraries and PlatformIO
pip install --upgrade pip
pip install -r requirements.txt

# 4. Compile the ESP32 firmware
pio run -d firmware
```

---

## 4. How to Use the System

### Step 1: Flash the Firmware to the ESP32
Connect your ESP32 board via USB and run:

```powershell
.\flash_esp32.ps1
# Or manually via PlatformIO:
.\.venv\Scripts\pio.exe run -d firmware -t upload
```
*(Alternatively, open [`firmware/esp32_battery_pulse/esp32_battery_pulse.ino`](firmware/esp32_battery_pulse/esp32_battery_pulse.ino) in the **Arduino IDE**, select **ESP32 Dev Module**, and click **Upload**).*

### Step 2: Run a Live Battery Pulse Measurement
Trigger the 8-second protocol ($2\text{ s}$ High-Z OCV $\to$ $1\text{ s}$ Pulse LOW $\to$ $5\text{ s}$ High-Z Relaxation) by specifying the reference resistor value $R_{\text{ref}}$ in Ohms:

```powershell
# Example with a 470 Ohm reference resistor:
.\run_pulse.ps1 -RRef 470

# Example with a 330 Ohm resistor and a ground-truth temperature label (e.g. 25.0 °C):
.\run_pulse.ps1 -RRef 330 -TempC 25.0
```

Or invoke the Python module directly with full CLI arguments:

```powershell
.\.venv\Scripts\python.exe -m src.acquire_pulse --port AUTO --r-ref 470 --temp-c 25.0 --soc-pct 80.0
```

### Step 3: Re-Analyze an Existing CSV File
You can re-run parameter extraction, EIS FFT analysis, and DRT inversion on any previously recorded CSV file at any time:

```powershell
.\.venv\Scripts\python.exe -m src.extract_parameters path/to/pulse_YYYYMMDD_HHMMSS.csv --r-ref 470
```

### Step 4: Run Offline Simulation & Temperature Calibration Verification
To test the entire pipeline without hardware connected (simulating a $20\,^\circ\text{C} \to 55\,^\circ\text{C}$ temperature sweep and training the polynomial & Scikit-Learn models):

```powershell
.\.venv\Scripts\python.exe -m src.simulate_and_verify
```

---

## 5. Generated Output Files

Every measurement run automatically creates a dedicated, timestamped subfolder inside `data/runs/run_YYYYMMDD_HHMMSS/` containing **5 files**:

| File Name | Format | Description |
| :--- | :---: | :--- |
| `pulse_<timestamp>.csv` | CSV | **Enriched Time-Series Data:** Microsecond timestamps, raw ADC counts, terminal voltage, overpotential, instantaneous current, power, apparent resistance, cumulative charge, and energy. |
| `pulse_<timestamp>_eis_spectrum.csv` | CSV | **Frequency-Domain EIS Spectrum:** FFT-derived complex impedance harmonics ($0.1\text{ Hz} - 200\text{ Hz}$) with $\text{Re}(Z)$, $\text{Im}(Z)$, magnitude $|Z|$, phase angle $\angle Z$, and effective capacitance $C_{\text{eff}}(f)$. |
| `pulse_<timestamp>_drt.csv` | CSV | **Distribution of Relaxation Times (DRT):** Tikhonov-regularized polarization density $\gamma(\ln \tau)$ across $\tau \in [0.3\text{ ms}, 15.8\text{ s}]$. |
| `pulse_<timestamp>_params.json` | JSON | **Complete Extracted Parameter Dictionary:** All 1-RC, 2-RC, EIS, DRT, and energetic parameters summarized in a single structured file. |
| `pulse_<timestamp>_plot.png` | PNG | **4-Panel Diagnostic Dashboard:** (1) Time-Domain Pulse & 1-RC/2-RC Fits, (2) Nyquist EIS Plot, (3) Bode Magnitude & Phase Spectrum, (4) DRT Relaxation Spectrum. |

---

## 6. Complete Parameter Reference Guide (What Each Parameter Measures)

### A. Open-Circuit, Current & Energetic Parameters (`params.json` & Time-Series CSV)

| Parameter Key | Unit | Mathematical Definition | Physical Meaning & What It Indicates |
| :--- | :---: | :--- | :--- |
| `v_ocv_v` | $\text{V}$ | Mean voltage during Phase 0 (`High-Z`) | **Open-Circuit Voltage ($V_{\text{OCV}}$):** Thermodynamic equilibrium potential of the cell. Directly indicates **State of Charge (SoC)** and baseline cell integrity. |
| `v_min_pulse_v` | $\text{V}$ | $\min(V(t))$ during Phase 1 | **Minimum Loaded Voltage:** Lowest terminal voltage reached under load; indicates whether the cell sags below safe cutoff thresholds. |
| `max_voltage_drop_mv` | $\text{mV}$ | $(V_{\text{OCV}} - V_{\text{min}}) \times 10^3$ | **Maximum Overpotential ($\Delta V_{\text{max}}$):** Total polarization voltage drop across internal impedances during the pulse. |
| `i_pulse_mean_ma` | $\text{mA}$ | $\text{mean}(V_{\text{pulse}}(t) / R_{\text{ref}}) \times 10^3$ | **Mean Pulse Current ($I_{\text{pulse}}$):** Average discharge current drawn from the battery during the 1-second load window. |
| `pulse_charge_mc` / `pulse_capacity_uah` | $\text{mC}$ / $\mu\text{Ah}$ | $\int_{t_1}^{t_2} I(t)\,dt$ | **Extracted Pulse Charge:** Total Coulombic charge removed from the cell during the pulse test. |
| `pulse_energy_mj` | $\text{mJ}$ | $\int_{t_1}^{t_2} V(t)I(t)\,dt$ | **Extracted Pulse Energy:** Total electrical energy delivered to the reference load $R_{\text{ref}}$ during the pulse. |
| `max_theoretical_power_mw` | $\text{mW}$ | $P_{\text{max}} = \frac{V_{\text{OCV}}^2}{4 R_s} \times 10^3$ | **Maximum Instantaneous Power Capability:** Peak power the cell can theoretically deliver to a matched load before voltage collapses below $0.5\,V_{\text{OCV}}$. |

---

### B. 1-RC Randles Equivalent Circuit Parameters

| Parameter Key | Unit | Mathematical Definition | Physical Meaning & What It Indicates |
| :--- | :---: | :--- | :--- |
| `r_s_ohm` | $\Omega$ | $R_s = \frac{V_{\text{OCV}} - V(t=0^+)}{I(t=0^+)}$ | **Ohmic Series Resistance ($R_s / R_0$):** Captured from the instantaneous vertical voltage drop within the first microseconds of the pulse. Represents **electrolyte ionic conductivity**, separator/matrix resistance, and current collector contact resistance. Because ionic mobility follows an Arrhenius relationship with temperature, **$R_s$ is the primary indicator of Internal Core Battery Temperature ($T_{\text{core}}$)** and electrolyte degradation. |
| `r_ct_ohm` / `r_ct_relax_ohm` | $\Omega$ | $R_{\text{ct}} = \frac{\Delta V_{\text{RC}}}{I_{\text{pulse}}}$ | **Charge-Transfer Resistance ($R_{\text{ct}}$):** Resistance of the exponential RC polarization curve during pulse (`r_ct_ohm`) and recovery (`r_ct_relax_ohm`). Reflects the kinetic barrier for electron/ion transfer at the electrode–electrolyte interface. Strongly indicates **State of Health (SoH), chemical aging, and SEI layer growth**. |
| `r_dc_total_ohm` | $\Omega$ | $R_{\text{DC}} = R_s + R_{\text{ct}}$ | **Total DC Internal Resistance:** Combined Ohmic and kinetic resistance governing steady-state voltage sag under load. |
| `c_dl_farad` / `c_dl_relax_farad` | $\text{F}$ | $C_{\text{dl}} = \frac{\tau}{R_{\text{ct}}}$ | **Double-Layer Capacitance ($C_{\text{dl}}$):** Electrical double-layer capacitance formed across the **porous electrode–electrolyte interface**. Tracks **structural/dielectric changes in the electrode matrix, pore wetting, and active electrochemically accessible surface area**. |
| `tau_sec` / `tau_relax_sec` | $\text{s}$ | $\tau = R_{\text{ct}} \cdot C_{\text{dl}}$ | **Characteristic Relaxation Time Constant ($\tau$):** Time required for the RC polarization to decay by $63.2\%$. Indicates the speed of interfacial charge equilibration. |
| `warburg_slope_v_s05` | $\text{V}/\sqrt{\text{s}}$ | $k_w$ in $V(t) \propto -k_w\sqrt{t}$ | **Warburg Diffusion Coefficient ($Z_w$):** Captures the $\sqrt{t}$ diffusive voltage tail during sustained pulses. Indicates **solid-state and pore-level ion diffusion bottlenecks** inside the electrode structure. |

---

### C. 2-RC Dual-Polarization Model Parameters (Fast vs. Slow Processes)

Single-exponential curves often blend fast surface effects with slower mass transport. The 2-RC model separates the recovery curve into two distinct physical time constants ($V(t) = V_{\infty} - \Delta V_1 e^{-t/\tau_1} - \Delta V_2 e^{-t/\tau_2}$):

| Parameter Key | Unit | Time Scale | Physical Meaning & What It Indicates |
| :--- | :---: | :---: | :--- |
| `r1_fast_ohm`, `c1_fast_farad`, `tau1_fast_sec` | $\Omega, \text{F}, \text{s}$ | $\sim 1 - 50\text{ ms}$ | **Fast Interfacial Process ($\tau_1$):** Captures rapid charge redistribution across the **SEI film, particle-to-particle contacts, and outer electrode-electrolyte interface**. |
| `r2_slow_ohm`, `c2_slow_farad`, `tau2_slow_sec` | $\Omega, \text{F}, \text{s}$ | $\sim 0.1 - 5.0\text{ s}$ | **Slow Diffusive / Charge-Transfer Process ($\tau_2$):** Captures slower **faradaic charge transfer and concentration relaxation** deep within the porous electrode structure. |
| `r_squared_2rc_relax` | $0 - 1$ | Dimensionless | **2-RC Goodness-of-Fit ($R^2$):** Coefficient of determination verifying how accurately the dual-RC model represents the measured recovery curve. |

---

### D. Frequency-Domain EIS & DRT Spectrum Parameters (`_eis_spectrum.csv` & `_drt.csv`)

By applying a Fast Fourier Transform (`numpy.fft.rfft`) to the step-current excitation $I(t)$ and voltage response $\Delta V(t)$, the system computes the complex impedance $Z(f) = -\frac{\mathcal{F}\{\Delta V(t)\}}{\mathcal{F}\{I(t)\}}$ across hundreds of harmonic frequencies simultaneously:

| Parameter Key / Column | Unit | Physical Meaning & What It Indicates |
| :--- | :---: | :--- |
| `freq_hz` | $\text{Hz}$ | **Excitation Harmonic Frequency ($f$):** Frequency bin from the FFT decomposition ($0.1\text{ Hz} - 200\text{ Hz}$). |
| `z_real_ohm` ($\text{Re}\{Z\}$) | $\Omega$ | **Real (Resistive) Impedance:** In-phase energy-dissipating resistance at frequency $f$. High-frequency $\text{Re}\{Z\}$ converges to $R_s$; low-frequency $\text{Re}\{Z\}$ converges to $R_s + R_{\text{ct}}$. |
| `z_imag_ohm` ($\text{Im}\{Z\}$) | $\Omega$ | **Imaginary (Reactive) Impedance:** Out-of-phase capacitive reactance at frequency $f$ (negative for capacitive battery interfaces). |
| `z_magnitude_ohm` ($|Z(f)|$) | $\Omega$ | **Impedance Magnitude:** Total opposition to AC current at frequency $f$, plotted on the Bode Magnitude graph. |
| `z_phase_deg` ($\angle Z(f)$) | $\text{deg}\ (^\circ)$ | **Impedance Phase Angle:** $\theta(f) = \arctan2(\text{Im}(Z), \text{Re}(Z))$. Measures how resistive ($0^\circ$) versus capacitive ($-90^\circ$) the cell behaves at each frequency. The minimum phase angle (`eis_min_phase_deg`) and its frequency (`eis_peak_phase_freq_hz`) pinpoint the characteristic frequency of the electrical double-layer. |
| `effective_capacitance_farad` | $\text{F}$ | **Frequency-Dependent Capacitance ($C_{\text{eff}}(f) = \frac{-1}{2\pi f \cdot \text{Im}(Z)}$):** Reveals how much of the porous electrode surface is accessible at fast vs. slow frequencies. |
| `eis_r0_ohm`, `eis_r1_ohm`, `eis_c1_farad` | $\Omega, \Omega, \text{F}$ | **Nyquist-Fitted Randles Parameters:** Circuit parameters fitted directly in the complex frequency plane using `impedance.py`. |
| `drt_peak1_tau_sec`, `drt_peak2_tau_sec` | $\text{s}$ | **DRT Characteristic Relaxation Peaks:** Dominant time-constant peaks identified without prior circuit assumptions via Tikhonov-regularized Distribution of Relaxation Times ($\gamma(\ln \tau)$), serving as an electrochemical fingerprint of the battery's internal layers. |

---

## 7. Repository Structure

```text
ESP32-Battery-EIS-Parameterization/
├── config.json                  # Hardware pins (GPIO 25/34), R_ref, pulse timings, Serial/VISA settings
├── requirements.txt             # Python package dependencies (scipy, impedance, pybop, scikit-learn, etc.)
├── setup_env.ps1                # One-click virtual environment & firmware build script
├── flash_esp32.ps1              # One-click ESP32 firmware upload script
├── run_pulse.ps1                # One-click pulse trigger & parameter extraction script
├── README.md                    # Complete documentation, installation & parameter reference guide
├── firmware/
│   ├── platformio.ini           # PlatformIO build configuration for ESP32
│   ├── src/
│   │   └── main.cpp             # ESP32 C++ firmware (Tri-state pulse, 40kHz edge burst, RAM buffer, NVS cal)
│   └── esp32_battery_pulse/
│       └── esp32_battery_pulse.ino # Arduino IDE compatible wrapper sketch
└── src/
    ├── __init__.py
    ├── acquire_pulse.py         # PySerial & PyVISA automated pulse controller & waveform downloader
    ├── extract_parameters.py    # 1-RC, 2-RC, FFT EIS, DRT & Energetics extraction + 4-panel plotting
    ├── calibrate_model.py       # Polynomial (ESP32 NVS) & Scikit-Learn temperature/SoH calibration
    └── simulate_and_verify.py   # Offline synthetic battery simulator & temperature sweep verifier
```
