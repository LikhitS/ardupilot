# Owner defaults

Put the owner's parameter set here as `plane.parm`, `copter.parm` or `rover.parm` (QuadPlane uses `plane.parm`).
Each board build writes it into the firmware as its defaults, so a new board, a parameter reset or a firmware
install starts from these values instead of ArduPilot's.

Only values that differ from ArduPilot's defaults belong here: the firmware has room for 8 KB (1 KB on small
boards). `embed_defaults.py` drops values that belong to one physical aircraft (sensor calibrations, device ids,
radio calibration, barometer ground pressure) and stops the build with a message if the file is too large.
