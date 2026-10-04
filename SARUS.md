# Sarus firmware

This repository is Sarus Aerospace's build of [ArduPilot](https://github.com/ArduPilot/ardupilot): Plane
(QuadPlane included), Copter and Rover, for Pixhawk-class flight controllers. Like ArduPilot it is free
software under the GNU GPL version 3.

## How it differs from ArduPilot

Version 4.7.1-S1 changes one thing, the name. The firmware reports `ArduPlane V4.7.1 Sarus-1`, and Copter
and Rover do the same; in flight it behaves exactly like ArduPilot 4.7.1. We kept the `Ardu… V` prefix on
purpose. Ground stations read it to work out which kind of vehicle is connected, and without it a QuadPlane
could be mistaken for a Copter.

Any later change in behaviour will be described here, one per release, together with the simulator results
that justify it.

## Branches and releases

Sarus work sits on `sarus-4.7.1`, which starts from ArduPilot's `Plane-4.7.1` tag (Copter-4.7.1 and
Rover-4.7.1 point at the same commit). Pushing the tag `sarus-v4.7.1-S1` publishes a stable release, and
`sarus-v4.7.1-S1-beta` a beta. A release holds firmware files named `Sarus-<Vehicle>-<version>-S<build>-<board>.apj`,
the Windows simulator, and the firmware list for that release.

The `sarus-manifest` branch keeps `manifest.json`, the list Sarus Operation Planner reads next to
ArduPilot's own when it fills the Install Firmware page.

## Building

`.github/workflows/sarus-firmware.yml` builds each board inside ArduPilot's own build container. By hand, the
same build is:

```
./waf configure --board CubeOrange
./waf plane copter rover
```

`Tools/sarus/make_manifest.py` writes the firmware list using ArduPilot's `Tools/scripts/generate_manifest.py`
unchanged, which is why board IDs, USB IDs and bootloader names match ArduPilot's exactly.
