# Sarus firmware

Sarus Aerospace's build of [ArduPilot](https://github.com/ArduPilot/ardupilot) (Plane including QuadPlane,
Copter and Rover) for Pixhawk-class flight controllers. It is free software under the GNU GPL v3, like ArduPilot.

## What differs from ArduPilot

| Version | Change |
|---|---|
| 4.7.1-S1 | Identity only: the firmware reports `ArduPlane V4.7.1 Sarus-1` (and likewise for Copter and Rover). Flight behaviour is identical to ArduPilot 4.7.1. The `Ardu… V` prefix is kept on purpose, because ground stations use it to recognise the vehicle type. |

Later behaviour changes are listed here, one per release, each with simulator evidence.

## Branches and releases

- `sarus-4.7.1`: Sarus changes on top of ArduPilot's `Plane-4.7.1` / `Copter-4.7.1` / `Rover-4.7.1` (same commit).
- Tag `sarus-v4.7.1-S1` publishes a stable release; `sarus-v4.7.1-S1-beta` publishes a beta.
- Each release contains `Sarus-<Vehicle>-<version>-S<build>-<board>.apj` firmware files, the Windows SITL
  simulators, and the release's firmware list.
- The branch `sarus-manifest` holds `manifest.json`, the list that Sarus Operation Planner's Install Firmware page
  reads alongside ArduPilot's own list.

## Building

The workflow `.github/workflows/sarus-firmware.yml` builds every board with ArduPilot's own build container:

```
./waf configure --board CubeOrange
./waf plane copter rover
```

`Tools/sarus/make_manifest.py` produces the firmware list with ArduPilot's `Tools/scripts/generate_manifest.py`,
so board ids, USB ids and bootloader strings are exactly ArduPilot's.
