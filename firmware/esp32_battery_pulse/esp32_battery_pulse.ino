// Arduino IDE compatible sketch (identical to firmware/src/main.cpp)
// Board Selection in Arduino IDE: "ESP32 Dev Module"
// Upload Speed: 921600, Serial Monitor Baud Rate: 921600
// Connections:
//   - Battery (-) -> ESP32 GND
//   - ESP32 GPIO 25 -> R_ref (e.g. 470 Ohm) -> Battery (+)
//   - Battery (+) -> ESP32 GPIO 34 (ADC1_CH6)

#include "../src/main.cpp"
