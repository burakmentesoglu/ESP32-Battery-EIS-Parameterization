# One-click script to trigger ESP32 pulse measurement and extract battery parameters
param(
    [string]$Port = "AUTO",
    [double]$RRef = 990.0,
    [Nullable[double]]$TempC = $null
)

$argsList = @("-m", "src.acquire_pulse", "--port", $Port, "--r-ref", $RRef)
if ($null -ne $TempC) {
    $argsList += @("--temp-c", $TempC)
}

.\.venv\Scripts\python.exe @argsList
