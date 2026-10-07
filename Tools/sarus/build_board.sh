#!/bin/bash
# Build Sarus Plane, Copter and Rover for one board, the way the Sarus firmware CI does.
#   Tools/sarus/build_board.sh BOARD OUTDIR
#
# Every board gets ArduPilot's own feature set, plus an 8 KB embedded-defaults area for the owner's parameter
# set wherever it fits. The only exceptions are listed in the "per-board choices" block below, each with its
# reason; a board without an exception is built exactly as ArduPilot builds it.
set -eo pipefail
B=$1
OUT=$2
LINE=$(grep -o 'V4\.[0-9]*' ArduPlane/version.h | head -1)

VEHICLES="plane copter rover"
declare -A EXTRA

# ---- per-board choices ----------------------------------------------------------------------------------
if [ "$LINE" = "V4.6" ] && [ "$B" = CubeOrangePlus ]; then
    # ArduPilot 4.6.3 leaves this board almost no flash (480 bytes for Copter, about 12 KB for Plane), and the
    # parameter lock's signature check needs about 13 KB. The VTOL build (Plane) drops two marine and
    # ground-vehicle features a QuadPlane never uses: Torqeedo electric boat motors and AIS ship receivers.
    # Rover keeps them. Copter is not built for this board on this line until a hover build needs it; the 4.7
    # line has room for all three.
    VEHICLES="plane rover"
    EXTRA[plane]="--define=HAL_TORQEEDO_ENABLED=0 --define=AP_AIS_ENABLED=0"
    echo "::notice::$B on $LINE: Plane built without Torqeedo and AIS; Copter not built (flash)"
fi
# ----------------------------------------------------------------------------------------------------------

mkdir -p "$OUT/$B"
declare -A EMB

# build a group of vehicles with the same extra options; record whether the defaults area fitted
build_group() {
    local vehicles=$1 extra=$2
    if ./waf configure --board "$B" --define=AP_PARAM_MAX_EMBEDDED_PARAM=8192 $extra && ./waf $vehicles 2>&1 | tee /tmp/waf.log; then
        for v in $vehicles; do EMB[$v]=1; done
    else
        # only a full flash may drop the defaults area; any other error fails the build
        grep -Eq "overflowed|will not fit|cannot move location counter" /tmp/waf.log || { echo "$B ($vehicles): build failed"; exit 1; }
        echo "::warning::$B ($vehicles): no room for the 8 KB embedded-defaults area; built without it"
        ./waf configure --board "$B" $extra
        ./waf $vehicles
        for v in $vehicles; do EMB[$v]=0; done
    fi
    for v in $vehicles; do cp "build/$B/bin/ardu$v.apj" "$OUT/$B/"; done
}

plain=""
for v in $VEHICLES; do
    if [ -n "${EXTRA[$v]}" ]; then build_group "$v" "${EXTRA[$v]}"; else plain="$plain $v"; fi
done
[ -z "$plain" ] || build_group "$plain" ""

for v in $VEHICLES; do
    apj="$OUT/$B/ardu$v.apj"
    # the owner's parameter set becomes the firmware's defaults (Tools/sarus/defaults/<vehicle>.parm, optional),
    # merged with any defaults the board ships with
    f="Tools/sarus/defaults/$v.parm"
    if [ -f "$f" ]; then
        [ "${EMB[$v]}" = 1 ] || { echo "$B ardu$v has no room for the owner's defaults ($f)"; exit 1; }
        python3 Tools/sarus/embed_defaults.py "$f" --apj "$apj"
    fi
    # prove the merge works on this firmware, on a scratch copy: a normal value goes in, a calibration does not
    if [ "${EMB[$v]}" = 1 ]; then
        printf 'LOG_DISARMED 1\nCOMPASS_OFS_X 9\n' > /tmp/sample.parm
        cp "$apj" /tmp/sample.apj
        python3 Tools/sarus/embed_defaults.py /tmp/sample.parm --apj /tmp/sample.apj
        python3 Tools/scripts/apj_tool.py --show /tmp/sample.apj > /tmp/sample.txt
        grep -q "LOG_DISARMED 1" /tmp/sample.txt || { echo "embedded defaults not written ($v)"; exit 1; }
        if grep -q "COMPASS_OFS_X" /tmp/sample.txt; then echo "a calibration value was embedded ($v)"; exit 1; fi
    fi
done

# the firmware must identify itself as Sarus and keep the ArduPilot prefix ground stations rely on; each binary is
# checked from the build that produced it, so read the version from the .apj image
for v in $VEHICLES; do
    case $v in plane) name=ArduPlane ;; copter) name=ArduCopter ;; rover) name=ArduRover ;; esac
    found=$(python3 - "$OUT/$B/ardu$v.apj" "$name" <<'PY'
import base64, json, re, sys, zlib
img = zlib.decompress(base64.b64decode(json.load(open(sys.argv[1]))['image']))
m = re.search(sys.argv[2].encode() + rb' V[0-9.]+ Sarus-[0-9]+', img)
print(m.group(0).decode() if m else '')
PY
)
    [ -n "$found" ] || { echo "firmware string missing in ardu$v"; exit 1; }
    echo "ardu$v reports: $found"
done
ls -la "$OUT/$B"
