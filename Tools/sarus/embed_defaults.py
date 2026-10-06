#!/usr/bin/env python3
"""
Prepare the owner's parameter file for embedding as firmware defaults (Tools/scripts/apj_tool.py --set-file).

Embedded defaults replace ArduPilot's own defaults, so a new board, a parameter reset or a firmware install
starts from the owner's values. The firmware has room for 8 KB of them (1 KB on small boards), so only values
that differ from ArduPilot's defaults belong in Tools/sarus/defaults/<vehicle>.parm.

Values that belong to one physical aircraft are dropped, because copying them to a fleet would be wrong on every
other aircraft: sensor calibrations and offsets, device ids, radio calibration, barometer ground pressure,
statistics and the format version.

  embed_defaults.py IN.parm OUT.parm [--max-bytes 8192]
"""
import argparse
import re
import sys

PER_AIRCRAFT = [
    r'^INS_.*(OFFS|SCAL|_ID|TCAL|ACC_BODYFIX|_POS)',
    r'^COMPASS_(OFS|DIA|ODI|MOT|DEV_ID|SCALE|PRIO\d_ID|ORIENT)',
    r'_DEVID\d*$', r'^BARO\d?_GND_', r'^BARO\d?_DEVID',
    r'^ARSPD\d?_OFFSET', r'^RC\d+_(MIN|MAX|TRIM)$',
    r'^STAT_', r'^FORMAT_VERSION$', r'^SYSID_SW_', r'^AHRS_TRIM_', r'^GND_ABS_PRESS', r'^SERIAL_PASS',
    r'^EK[23]_.*_POS', r'^MIS_TOTAL$', r'^FENCE_TOTAL$', r'^RALLY_TOTAL$',
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('infile')
    ap.add_argument('outfile')
    ap.add_argument('--max-bytes', type=int, default=8192)
    a = ap.parse_args()

    keep, dropped, bad = [], [], []
    for n, raw in enumerate(open(a.infile, encoding='utf-8', errors='replace'), 1):
        line = raw.split('#')[0].strip()
        if not line:
            continue
        parts = re.split(r'[\s,]+', line)
        if len(parts) < 2 or not re.match(r'^[A-Z][A-Z0-9_]{0,15}$', parts[0]):
            bad.append('%d: %s' % (n, raw.strip()))
            continue
        name, value = parts[0], parts[1]
        try:
            float(value)
        except ValueError:
            bad.append('%d: %s' % (n, raw.strip()))
            continue
        if any(re.search(p, name) for p in PER_AIRCRAFT):
            dropped.append(name)
            continue
        keep.append('%s %s' % (name, value))

    if bad:
        sys.exit('lines that are not "NAME VALUE":\n  ' + '\n  '.join(bad[:20]))
    text = '\n'.join(keep) + '\n'
    size = len(text.encode('ascii'))
    print('%d parameters kept (%d bytes), %d per-aircraft values dropped%s' %
          (len(keep), size, len(dropped), (': ' + ', '.join(dropped[:12]) + (' ...' if len(dropped) > 12 else '')) if dropped else ''))
    if size > a.max_bytes:
        sys.exit('too large for the firmware (%d of %d bytes): keep only the values that differ from ArduPilot\'s defaults'
                 % (size, a.max_bytes))
    open(a.outfile, 'w', encoding='ascii', newline='\n').write(text)


if __name__ == '__main__':
    main()
