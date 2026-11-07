#include <Arduino.h>
#include <Preferences.h>
#include "driver/gpio.h"

// ============================================================================
// Hardware Pin Definitions (Per Prototype Guide Section 2.3)
// ============================================================================
constexpr int PIN_PULSE = 25; // Tri-State Pulse Pin: GPIO 25 -> R_ref -> Bat (+)
constexpr int PIN_ADC   = 34; // Voltage Sense Pin:   Bat (+) -> GPIO 34 (ADC1_CH6)

// Reference resistor value (Ohms) - configurable via 'RREF <val>' serial command
float g_r_ref_ohm = 470.0f;

// Calibration polynomial coefficients: Temp(C) = c2 * Rs^2 + c1 * Rs + c0
float g_cal_c2 = 0.0f;
float g_cal_c1 = -1.5f;
float g_cal_c0 = 60.0f;

Preferences prefs;

// ============================================================================
// Sample Buffer in RAM (Zero UART jitter during pulse transient)
// ============================================================================
struct SamplePoint {
    uint32_t time_us;
    uint16_t voltage_mv;
    uint16_t raw_and_phase; // bits [11:0] = 12-bit raw_adc, bits [15:12] = phase (0, 1, 2)
};

constexpr size_t MAX_SAMPLES = 10000;
SamplePoint* g_samples = nullptr;
size_t g_sample_count = 0;

// ============================================================================
// Tri-State Control Helpers (Section 2.2)
// ============================================================================
inline void setPinHighZ() {
    // Mode 1 & 3: Open-Circuit / High Impedance (Zero current flow)
    pinMode(PIN_PULSE, INPUT);
}

inline void setPinPulseLow() {
    // Mode 2: Square Wave Pulse (Active sink to 0V GND through R_ref)
    digitalWrite(PIN_PULSE, LOW);
    pinMode(PIN_PULSE, OUTPUT);
    digitalWrite(PIN_PULSE, LOW);
}

inline void recordSample(uint32_t t0_us, uint8_t phase) {
    if (!g_samples || g_sample_count >= MAX_SAMPLES) return;
    uint32_t now_us = micros() - t0_us;
    uint16_t raw = analogRead(PIN_ADC) & 0x0FFF;
    uint16_t mv  = analogReadMilliVolts(PIN_ADC);
    uint16_t packed = (static_cast<uint16_t>(phase & 0x0F) << 12) | raw;
    g_samples[g_sample_count++] = {now_us, mv, packed};
}

// ============================================================================
// Execute Controlled Pulse Protocol (Section 6 - Step 2)
// ============================================================================
void runPulseExperiment(uint32_t ocv_ms, uint32_t pulse_ms, uint32_t relax_ms, uint32_t sample_period_us) {
    g_sample_count = 0;
    setPinHighZ();
    delay(50); // Settle in High-Z before starting timer

    Serial.println("# STATUS: RUNNING_PULSE");
    uint32_t t0_us = micros();

    // ------------------------------------------------------------------------
    // Phase 0: Pre-Pulse High-Z (Open Circuit Voltage V_OCV)
    // ------------------------------------------------------------------------
    uint32_t phase0_end_us = ocv_ms * 1000UL;
    uint32_t next_sample_us = 0;
    while ((micros() - t0_us) < phase0_end_us) {
        uint32_t elapsed = micros() - t0_us;
        if (elapsed >= next_sample_us) {
            recordSample(t0_us, 0);
            next_sample_us += sample_period_us;
        }
    }

    // Record immediately before switching to LOW
    recordSample(t0_us, 0);

    // ------------------------------------------------------------------------
    // Phase 1: Square Wave Pulse (GPIO 25 = OUTPUT LOW)
    // ------------------------------------------------------------------------
    uint32_t pulse_start_us = micros() - t0_us;
    setPinPulseLow();

    // High-speed burst sampling for the first 5 ms (captures Ohmic drop R_s at t=0+)
    uint32_t burst1_end_us = pulse_start_us + 5000UL;
    while ((micros() - t0_us) < burst1_end_us) {
        recordSample(t0_us, 1);
        delayMicroseconds(25);
    }

    // Standard sampling for the remainder of the pulse
    uint32_t phase1_end_us = pulse_start_us + (pulse_ms * 1000UL);
    next_sample_us = micros() - t0_us;
    while ((micros() - t0_us) < phase1_end_us) {
        uint32_t elapsed = micros() - t0_us;
        if (elapsed >= next_sample_us) {
            recordSample(t0_us, 1);
            next_sample_us += sample_period_us;
        }
    }

    // Record final sample under load before releasing pulse
    recordSample(t0_us, 1);

    // ------------------------------------------------------------------------
    // Phase 2: Relaxation / Recovery (GPIO 25 = INPUT / High-Z)
    // ------------------------------------------------------------------------
    uint32_t relax_start_us = micros() - t0_us;
    setPinHighZ();

    // High-speed burst sampling for the first 5 ms of recovery
    uint32_t burst2_end_us = relax_start_us + 5000UL;
    while ((micros() - t0_us) < burst2_end_us) {
        recordSample(t0_us, 2);
        delayMicroseconds(25);
    }

    // Standard sampling for the remainder of relaxation
    uint32_t phase2_end_us = relax_start_us + (relax_ms * 1000UL);
    next_sample_us = micros() - t0_us;
    while ((micros() - t0_us) < phase2_end_us) {
        uint32_t elapsed = micros() - t0_us;
        if (elapsed >= next_sample_us) {
            recordSample(t0_us, 2);
            next_sample_us += sample_period_us;
        }
    }

    // ------------------------------------------------------------------------
    // Stream Recorded Data to PC over Serial
    // ------------------------------------------------------------------------
    Serial.println("# BEGIN_PULSE_DATA");
    Serial.printf("# META: r_ref_ohm=%.2f,ocv_ms=%lu,pulse_ms=%lu,relax_ms=%lu,samples=%u\n",
                  g_r_ref_ohm, (unsigned long)ocv_ms, (unsigned long)pulse_ms,
                  (unsigned long)relax_ms, (unsigned int)g_sample_count);
    Serial.println("time_us,voltage_mv,raw_adc,phase");

    for (size_t i = 0; i < g_sample_count; ++i) {
        uint16_t raw_adc = g_samples[i].raw_and_phase & 0x0FFF;
        uint8_t phase = (g_samples[i].raw_and_phase >> 12) & 0x0F;
        Serial.printf("%lu,%u,%u,%u\n",
                      (unsigned long)g_samples[i].time_us,
                      g_samples[i].voltage_mv,
                      raw_adc,
                      phase);
    }
    Serial.println("# END_PULSE_DATA");
}

void setup() {
    setPinHighZ();
    Serial.begin(921600);

    g_samples = static_cast<SamplePoint*>(malloc(MAX_SAMPLES * sizeof(SamplePoint)));

    // Configure ESP32 ADC1_CH6 (GPIO 34) for 12-bit resolution and 11dB attenuation (up to ~3.1V-3.3V)
    analogReadResolution(12);
    analogSetPinAttenuation(PIN_ADC, ADC_11db);

    prefs.begin("bat_param", false);
    g_r_ref_ohm = prefs.getFloat("r_ref", 470.0f);
    g_cal_c2    = prefs.getFloat("cal_c2", 0.0f);
    g_cal_c1    = prefs.getFloat("cal_c1", -1.5f);
    g_cal_c0    = prefs.getFloat("cal_c0", 60.0f);

    delay(200);
    Serial.println("# ESP32 Battery Pulse Parameterizer Ready");
}

void loop() {
    if (!Serial.available()) {
        delay(5);
        return;
    }

    String cmd = Serial.readStringUntil('\n');
    cmd.trim();
    if (cmd.length() == 0) return;

    if (cmd.equalsIgnoreCase("PING") || cmd.equalsIgnoreCase("STATUS")) {
        setPinHighZ();
        uint32_t mv = analogReadMilliVolts(PIN_ADC);
        Serial.printf("# READY: v_ocv_mv=%lu,r_ref_ohm=%.2f,cal=[%.4f,%.4f,%.4f]\n",
                      (unsigned long)mv, g_r_ref_ohm, g_cal_c2, g_cal_c1, g_cal_c0);
    } else if (cmd.equalsIgnoreCase("OCV")) {
        setPinHighZ();
        delay(10);
        uint32_t sum_mv = 0;
        for (int i = 0; i < 16; ++i) {
            sum_mv += analogReadMilliVolts(PIN_ADC);
            delay(2);
        }
        Serial.printf("OCV_MV:%lu\n", (unsigned long)(sum_mv / 16));
    } else if (cmd.startsWith("PULSE")) {
        uint32_t ocv_ms = 2000;
        uint32_t pulse_ms = 1000;
        uint32_t relax_ms = 5000;
        uint32_t period_us = 1000; // 1 kHz base + 40 kHz edge burst
        sscanf(cmd.c_str(), "PULSE %lu %lu %lu %lu", &ocv_ms, &pulse_ms, &relax_ms, &period_us);
        runPulseExperiment(ocv_ms, pulse_ms, relax_ms, period_us);
    } else if (cmd.startsWith("RREF")) {
        float val = 470.0f;
        if (sscanf(cmd.c_str(), "RREF %f", &val) == 1 && val > 10.0f) {
            g_r_ref_ohm = val;
            prefs.putFloat("r_ref", g_r_ref_ohm);
            Serial.printf("# UPDATED R_REF: %.2f Ohm\n", g_r_ref_ohm);
        }
    } else if (cmd.startsWith("CAL")) {
        float c2 = 0.0f, c1 = 0.0f, c0 = 0.0f;
        if (sscanf(cmd.c_str(), "CAL %f %f %f", &c2, &c1, &c0) == 3) {
            g_cal_c2 = c2;
            g_cal_c1 = c1;
            g_cal_c0 = c0;
            prefs.putFloat("cal_c2", g_cal_c2);
            prefs.putFloat("cal_c1", g_cal_c1);
            prefs.putFloat("cal_c0", g_cal_c0);
            Serial.printf("# UPDATED CALIBRATION: c2=%.6f, c1=%.6f, c0=%.6f\n", g_cal_c2, g_cal_c1, g_cal_c0);
        }
    } else {
        Serial.println("# ERROR: Unknown command. Valid: PING, OCV, PULSE [ocv_ms pulse_ms relax_ms period_us], RREF <ohm>, CAL <c2> <c1> <c0>");
    }
}
