# ESP32 Battery Pulse Parameterization & EIS Suite — Complete Technical Installation & Setup Guide

This document provides a comprehensive, step-by-step technical guide for setting up, building, flashing, and running the **ESP32-Battery-EIS-Parameterization** system from scratch on a **freshly formatted Windows machine** with no prior development tools (no C/C++ compilers, no Python, no Git, and no PlatformIO installed).

---

## 1. System Architecture & Prerequisites

The system combines embedded ESP32 firmware for microsecond-accurate tri-state pulse switching and RAM-buffered ADC acquisition with a host-side Python scientific pipeline for Equivalent Circuit Modeling (1-RC Randles & 2-RC Dual-Polarization), FFT Electrochemical Impedance Spectroscopy (EIS), Distribution of Relaxation Times (DRT), and temperature/SoH calibration.

### 1.1. Programming Languages Used
| Language | Role in the Project |
| :--- | :--- |
| **C / C++ (Arduino-ESP32 Framework)** | ESP32 embedded firmware (`firmware/src/main.cpp`), tri-state GPIO load switching (`GPIO 25`), 12-bit ADC burst sampling (`GPIO 34`), RAM sample buffering, serial command parsing (`921600 baud`), and NVS flash calibration storage (`Preferences`). |
| **Python (3.10 – 3.13)** | Host-side automation (`src/acquire_pulse.py`), Randles/EIS/DRT parameter extraction (`src/extract_parameters.py`), polynomial & Scikit-Learn calibration (`src/calibrate_model.py`), and offline synthetic verification (`src/simulate_and_verify.py`). |
| **PowerShell & Batch (WinForms)** | One-click Windows GUI installer (`Easy_Setup_Wizard.bat`) and quick-start automation scripts (`setup_env.ps1`, `flash_esp32.ps1`, `run_pulse.ps1`). |

### 1.2. Frameworks, Compilers & Toolchain
* **Embedded Build System:** **PlatformIO Core (`pio` v6.1+)** running inside the isolated project `.venv`.
* **Embedded Platform & Framework:** **Espressif 32 (`espressif32`)** with `framework-arduinoespressif32` targeting `esp32dev`.
* **C/C++ Cross-Compiler Toolchain:** Automatically downloaded and managed by PlatformIO (`toolchain-xtensa-esp-elf`, `tool-esptool`, `tool-scons`).
* *(Optional Alternative)* **Arduino IDE:** Supported via `firmware/esp32_battery_pulse/esp32_battery_pulse.ino`.

### 1.3. Host Python Libraries (`.venv` Virtual Environment)
All Python packages are installed cleanly inside a project-local `.venv` via `requirements.txt`:
* **`pyserial`**: High-speed USB-UART communication (`921600 baud`) with the ESP32.
* **`pyvisa` & `pyvisa-py`**: Optional hybrid oscilloscope control over USB/LAN.
* **`scipy` & `numpy`**: Nonlinear least-squares curve fitting (`curve_fit`), FFT complex impedance computation (`numpy.fft.rfft`), numerical integration, and Tikhonov DRT inversion.
* **`impedance`**: Frequency-domain Randles equivalent circuit fitting (`R0-p(R1,C1)`).
* **`pybop`**: Time-domain battery parameterization & optimization ecosystem.
* **`scikit-learn`, `pandas`, `matplotlib`**: Data processing, 4-panel diagnostic plotting (Transient, Nyquist, Bode, DRT), and ML temperature/SoH regression.

### 1.4. Hardware & Driver Requirements
* **1× ESP32 Development Board** (`ESP32 Dev Module`, e.g., ESP32-WROOM-32).
* **1× Reference Resistor ($R_{\text{ref}}$):** $100\,\Omega - 1\,\text{k}\Omega$ (e.g., $330\,\Omega$, $470\,\Omega$, or $990\,\Omega$).
* **1× Breadboard & 3× Jumper Wires.**
* **1× Battery Under Test ($V_{\text{OCV}} \le 3.3\text{ V}$)** for direct pin connection.
* **Data-Capable USB Cable** (Micro-USB or USB-C depending on the board; ensure it is not a charge-only cable).
* **USB-to-UART Bridge Driver:** **Silicon Labs CP210x** (or **CH340/CH341** depending on your board's USB-serial chip).

---

## 2. Automated One-Click Setup (Recommended)

On any Windows 10/11 PC (including a freshly formatted machine):

1. Download **`Easy_Setup_Wizard.bat`** (or clone the full repository).
2. Double-click **`Easy_Setup_Wizard.bat`**.
3. Choose the installation folder using **Browse...** and click **Next >**.
4. The wizard automatically runs all **10 steps**:
   - Configures PowerShell `RemoteSigned` execution policy and workspace folders.
   - Installs **Python 3.12** and **Git** via `winget` (if missing) and clones the GitHub repository.
   - Downloads and installs the **Silicon Labs CP210x** USB-UART driver (if missing).
   - Creates `.venv` and installs all scientific Python packages + PlatformIO (`requirements.txt`).
   - Optionally installs **Visual Studio Code** with PlatformIO, Python, and C/C++ extensions.
   - Compiles and verifies the ESP32 firmware (`firmware\.pio\build\esp32dev\firmware.bin`).
   - Runs the offline synthetic sweep (`src.simulate_and_verify`) to verify the analysis pipeline.
   - Prompts to flash any connected ESP32 board(s) over USB.
   - Creates ready-to-use desktop shortcuts in `Desktop\apps\Battery-EIS\`.

---

## 3. Manual Step-by-Step Installation (From a Clean Windows PC)

### Step 1: Enable PowerShell Script Execution
On a fresh Windows installation, PowerShell blocks `.ps1` scripts by default. Open **PowerShell** and run:

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser -Force
```

### Step 2: Install Python 3.12, Git, and USB-UART Drivers

#### 2.1. Install Python 3.12
```powershell
winget install --id Python.Python.3.12 -e --source winget --accept-package-agreements --accept-source-agreements
```
> **Note:** Supported Python versions are `3.10` – `3.13`. Verify installation with `python --version`.

#### 2.2. Install Git
```powershell
winget install --id Git.Git -e --source winget --accept-package-agreements --accept-source-agreements
```

#### 2.3. Install Silicon Labs CP210x USB-UART Driver (Administrator PowerShell)
```powershell
$driverDir = "C:\Users\$env:USERNAME\Desktop\apps\drivers\CP210x"
New-Item -ItemType Directory -Force -Path $driverDir | Out-Null
curl.exe -4 -L -o "$driverDir\CP210x.zip" "https://dl.espressif.com/dl/idf-installer/CP210x_Universal_Windows_Driver.zip"
Expand-Archive -Path "$driverDir\CP210x.zip" -DestinationPath $driverDir -Force
pnputil /add-driver "$driverDir\silabser.inf" /install
```

#### 2.4. Refresh Environment Variables in Current PowerShell Session
```powershell
$env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
```

### Step 3: Clone the Repository & Create the Python Virtual Environment
```powershell
New-Item -ItemType Directory -Force -Path "C:\Users\$env:USERNAME\Desktop\apps\projects" | Out-Null
Set-Location "C:\Users\$env:USERNAME\Desktop\apps\projects"
git clone "https://github.com/burakmentesoglu/ESP32-Battery-EIS-Parameterization.git"
Set-Location "ESP32-Battery-EIS-Parameterization"

python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\pip.exe install -r requirements.txt
```

> **Warning — Do NOT run `pip install serial`:**  
> Always install **`pyserial`** (provided in `requirements.txt`). An unrelated package named `serial` on PyPI overwrites `pyserial` and causes `AttributeError: module 'serial' has no attribute 'Serial'`. If this occurs, run:
> ```powershell
> .\.venv\Scripts\python.exe -m pip uninstall -y serial
> .\.venv\Scripts\python.exe -m pip install --force-reinstall pyserial
> ```

Verify all packages import cleanly:
```powershell
.\.venv\Scripts\python.exe -c "import pyvisa, serial, scipy, numpy, matplotlib, pandas, sklearn, impedance, pybop; assert hasattr(serial, 'Serial'); print('ALL_IMPORTS_OK')"
```

### Step 4: Compile & Flash the ESP32 Firmware

1. **Pre-compile the firmware** (downloads the Espressif32 PlatformIO toolchain automatically):
   ```powershell
   .\.venv\Scripts\pio.exe run -d firmware
   ```
   Verified binary outputs are created at:
   - `firmware\.pio\build\esp32dev\firmware.bin`
   - `firmware\.pio\build\esp32dev\bootloader.bin`
   - `firmware\.pio\build\esp32dev\partitions.bin`

2. **Connect your ESP32 via USB and flash it:**
   ```powershell
   .\flash_esp32.ps1
   # Or specify a specific COM port:
   .\.venv\Scripts\pio.exe run -d firmware -t upload --upload-port COM9
   ```
   > **Important:** Close any open Serial Monitor (`pio device monitor`, Arduino Serial Monitor, etc.) before running `acquire_pulse.py`, because Windows allows only one program to hold a COM port open at a time.

---

## 4. Breadboard Wiring & Running Your First Measurement

### 4.1. 3-Wire Breadboard Connections
1. **Common Ground:** Connect `Battery (-)` directly to **`ESP32 GND`**.
2. **Pulse Load Path:** Connect **`ESP32 GPIO 25`** to one leg of the reference resistor **$R_{\text{ref}}$** (e.g., `330` or `470` $\Omega$). Connect the other leg of $R_{\text{ref}}$ to `Battery (+)`.
3. **ADC Sense Line:** Connect **`ESP32 GPIO 34`** (`ADC1_CH6`) to the **battery side** of $R_{\text{ref}}$ (the same breadboard row as `Battery (+)`).

> **Safety Reminder:** Never connect batteries with $V_{\text{OCV}} > 3.3\text{ V}$ (such as $3.7\text{ V} - 4.2\text{ V}$ Li-ion or $9\text{ V}$ blocks) directly to ESP32 pins.

### 4.2. Run a Live Pulse Measurement
```powershell
.\run_pulse.ps1 -RRef 470
# Optionally record ground-truth temperature (e.g. 25.0 °C):
.\run_pulse.ps1 -RRef 470 -TempC 25.0
```
Each run creates a timestamped folder under `data/runs/run_YYYYMMDD_HHMMSS/` containing the enriched time-series `.csv`, frequency-domain `_eis_spectrum.csv`, `_drt.csv`, `_params.json`, and 4-panel `_plot.png`.

### 4.3. Run Offline Pipeline Verification (No Hardware Needed)
```powershell
.\.venv\Scripts\python.exe -m src.simulate_and_verify
```

---

## 5. Quick-Launch Shortcuts (`Desktop\apps\Battery-EIS`)

When you run `Easy_Setup_Wizard.bat`, five ready-to-use batch launchers are created in `Desktop\apps\Battery-EIS\`:

| Script Name | Description |
| :--- | :--- |
| **`1_Run_Battery_Pulse.bat`** | Prompts for $R_{\text{ref}}$ ($\Omega$) and optional temperature ($^\circ\text{C}$), triggers the ESP32 pulse, and saves all 5 output files. |
| **`2_Flash_ESP32_Firmware.bat`** | Uploads the compiled firmware to the connected ESP32 via PlatformIO. |
| **`3_Offline_Simulation_Test.bat`** | Runs `src.simulate_and_verify` and opens the generated calibration plot. |
| **`4_Analyze_Existing_CSV.bat`** | Lets you drag-and-drop any recorded `.csv` file to re-run Randles/2-RC/EIS/DRT extraction. |
| **`5_Open_Project_Shell.bat`** | Opens a terminal inside the repository with `.venv` pre-activated. |

---

## 6. Troubleshooting

1. **No COM Port Detected When ESP32 Is Plugged In:**
   * **Cause:** Charge-only USB cable (missing D+/D- data wires) or missing CP210x / CH340 USB-UART driver.
   * **Solution:** Swap to a verified data USB cable and run Step 2.3 to install the Silicon Labs CP210x driver.
2. **Phase 1 (Pulse) Voltage Drops to `142 mV` (`raw_adc = 0`):**
   * **Cause 1:** `GPIO 34` is connected to the `GPIO 25` leg of $R_{\text{ref}}$ instead of the `Battery (+)` leg. Verify `GPIO 25` is alone on one leg, while `GPIO 34` and `Battery (+)` share the opposite leg.
   * **Cause 2:** The test cell is heavily depleted (e.g., a dead coin cell with $R_{\text{internal}} \gg R_{\text{ref}}$), causing terminal voltage to collapse below the ESP32 ADC's ~150 mV floor. Increase $R_{\text{ref}}$ (e.g. $1\,\text{k}\Omega - 4.7\,\text{k}\Omega$) or test with a fresh $1.5\text{ V}$ AA or $3.0\text{ V}$ CR2032 cell.
3. **`PermissionError(13, 'Access is denied')` on COM Port:**
   * **Cause:** Another terminal or IDE serial monitor is keeping the COM port open.
   * **Solution:** Close the serial monitor and re-run `.\run_pulse.ps1`.
4. **PowerShell Blocks `.ps1` Scripts (`cannot be loaded because running scripts is disabled`):**
   * **Solution:** Run `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser -Force` or use the `.bat` shortcuts created by `Easy_Setup_Wizard.bat`.
