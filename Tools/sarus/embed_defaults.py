#!/usr/bin/env python3
"""
Put the owner's parameter set into Sarus firmware as its embedded defaults.

Embedded defaults replace ArduPilot's own defaults, so a new board, a parameter reset or a firmware install
starts from the owner's values. The firmware has room for 8 KB of them, so only values that differ from
ArduPilot's defaults belong in Tools/sarus/defaults/<vehicle>.parm.

Values that belong to one physical aircraft are dropped, because copying them to a fleet would be wrong on every
other aircraft: sensor calibrations and offsets, device ids, radio calibration, barometer ground pressure,
statistics and the format version.

  embed_defaults.py IN.parm OUT.parm [--max-bytes 8192]   prepare a file
  embed_defaults.py IN.parm --apj FIRMWARE.apj            merge it into a firmware's embedded defaults

With --apj the owner's values are merged into whatever defaults the board already embeds (some boards ship their
own); where both set a parameter, the owner's value wins. The firmware's own size limit applies.
"""
import argparse
import os
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

SPLIT = re.compile(r'[\s,]+')


def read_owner(path):
    keep, dropped, bad = [], [], []
    for n, raw in enumerate(open(path, encoding='utf-8', errors='replace'), 1):
        line = raw.split('#')[0].strip()
        if not line:
            continue
        parts = SPLIT.split(line)
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
        keep.append((name, value))
    if bad:
        sys.exit('lines that are not "NAME VALUE":\n  ' + '\n  '.join(bad[:20]))
    return keep, dropped


def as_text(pairs):
    return ''.join('%s %s\n' % p for p in pairs)


def merge_into_apj(apj, keep, dropped):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'scripts'))
    from apj_tool import embedded_defaults
    fw = embedded_defaults(apj)
    if not fw.find():
        sys.exit('%s has no embedded defaults area' % apj)
    merged = {}
    for line in fw.contents().decode('ascii', 'replace').splitlines():
        parts = SPLIT.split(line.split('#')[0].strip())
        if len(parts) >= 2:
            merged[parts[0]] = parts[1]
    board = len(merged)
    for name, value in keep:
        merged[name] = value
    text = as_text(merged.items())
    print('%s: %d board defaults + %d owner values = %d parameters, %d of %d bytes; %d per-aircraft values dropped'
          % (os.path.basename(apj), board, len(keep), len(merged), len(text), fw.max_len, len(dropped)))
    fw.set_contents(text)  # stops with an error if the firmware has no room
    fw.save()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('infile')
    ap.add_argument('outfile', nargs='?')
    ap.add_argument('--max-bytes', type=int, default=8192)
    ap.add_argument('--apj', help='merge into this firmware file instead of writing OUT.parm')
    a = ap.parse_args()
    if not a.outfile and not a.apj:
        ap.error('give OUT.parm or --apj FIRMWARE.apj')

    keep, dropped = read_owner(a.infile)
    if a.apj:
        merge_into_apj(a.apj, keep, dropped)
        return

    text = as_text(keep)
    size = len(text.encode('ascii'))
    print('%d parameters kept (%d bytes), %d per-aircraft values dropped%s' %
          (len(keep), size, len(dropped), (': ' + ', '.join(dropped[:12]) + (' ...' if len(dropped) > 12 else '')) if dropped else ''))
    if size > a.max_bytes:
        sys.exit('too large for the firmware (%d of %d bytes): keep only the values that differ from ArduPilot\'s defaults'
                 % (size, a.max_bytes))
    open(a.outfile, 'w', encoding='ascii', newline='\n').write(text)


if __name__ == '__main__':
    main()
