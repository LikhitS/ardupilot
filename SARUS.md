<!-- lint-allow: unlock -->
# Sarus firmware

This repository is Sarus Aerospace's build of [ArduPilot](https://github.com/ArduPilot/ardupilot): Plane
(QuadPlane included), Copter and Rover, for Pixhawk-class flight controllers. Like ArduPilot it is free
software under the GNU GPL version 3.

## Two lines

Sarus firmware comes in two lines with the same Sarus changes. The 4.7 line is built on ArduPilot 4.7.1, the
newest stable release. The 4.6 line is built on ArduPilot 4.6.3, the last release of the older and longer
proven 4.6 series. Install Firmware in Sarus Operation Planner lists both and asks which one to load.

The firmware reports itself as, for example, `ArduPlane V4.6.3 Sarus-2` or `ArduCopter V4.7.1 Sarus-2`. The
number after "Sarus-" counts Sarus releases and means the same changes on both lines. We kept the `Ardu… V`
prefix on purpose: ground stations read it to work out which kind of vehicle is connected, and without it a
QuadPlane could be mistaken for a Copter.

## How it differs from ArduPilot

Sarus-1 changed only the name. Sarus-2 adds the parameter lock described in `Tools/sarus/SARUS_LOCK.md`.
Anyone can connect, read parameters, plan and fly, but setup changes (parameter writes, calibrations,
resets, file writes and similar) need the owner's admin password, which Sarus Operation Planner turns into a
signature the aircraft checks. Firmware built before an owner key exists has the lock switched off and
behaves exactly like ArduPilot.

Any later change in behaviour will be described here, one per release, together with the simulator results
that justify it.

## Branches and releases

The 4.7 line sits on `sarus-4.7.1`, which starts from ArduPilot's `Plane-4.7.1` tag (Copter and Rover 4.7.1
point at the same commit). The 4.6 line sits on `sarus-4.6.3`, which starts from `Copter-4.6.3`. That commit
is `Plane-4.6.3` and `Rover-4.6.3` plus one change to Copter's version file, so all three vehicles are built
from exactly their official 4.6.3 source.

A tag publishes a release: `sarus-v4.7.1-S2` or `sarus-v4.6.3-S2` for stable, with `-beta` on the end for a
beta. Each release carries the firmware files, named `Sarus-<Vehicle>-<version>-S<build>-<board>.apj`, along
with the Windows simulator and that release's firmware list. The `sarus-manifest` branch keeps
`manifest.json`, the list Sarus Operation Planner reads next to ArduPilot's own, and holds one current
release per line. Publishing for 4.6 never removes 4.7.

## Building

`.github/workflows/sarus-firmware.yml` builds each board inside ArduPilot's own build container, runs the
lock tests on the simulator, and builds the Windows simulators. By hand, the same board build is:

```
./waf configure --board CubeOrange
./waf plane copter rover
```

`Tools/sarus/make_manifest.py` writes the firmware list using ArduPilot's `Tools/scripts/generate_manifest.py`
unchanged, which is why board IDs, USB IDs and bootloader names match ArduPilot's exactly.
