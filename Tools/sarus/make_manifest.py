#!/usr/bin/env python3
'''
Sarus: build the firmware list (manifest.json) for a Sarus firmware release.

Uses ArduPilot's own Tools/scripts/generate_manifest.py unchanged, so board ids, USB ids, bootloader
strings and platform names are exactly ArduPilot's and ground stations detect boards the same way.
Only the download URLs differ: they point at the Sarus release assets, whose names start with "Sarus-".

Input:  <fwdir>/<board>/{arduplane,arducopter,ardurover}.apj   (one folder per board)
Output: <outdir>/assets/Sarus-<Vehicle>-<version>-S<build>-<board>.apj
        <outdir>/manifest-release.json   entries of this release only

usage: make_manifest.py --fwdir fw --outdir out --version 4.7.1 --build 1 --release-type stable
                        --git-sha <sha> --asset-base https://github.com/<owner>/<repo>/releases/download/<tag>
'''
import argparse
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'scripts'))
import generate_manifest  # noqa: E402

PLACEHOLDER = 'sarus:'

VEHICLES = {
    # apj file name: (manifest vehicle directory, firmware name prefix as in THISFIRMWARE)
    'arduplane.apj': ('Plane', 'ArduPlane'),
    'arducopter.apj': ('Copter', 'ArduCopter'),
    'ardurover.apj': ('Rover', 'ArduRover'),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--fwdir', required=True)
    ap.add_argument('--outdir', required=True)
    ap.add_argument('--version', required=True, help='ArduPilot base version, e.g. 4.7.1')
    ap.add_argument('--build', required=True, help='Sarus build number, e.g. 1')
    ap.add_argument('--release-type', choices=['stable', 'beta'], required=True)
    ap.add_argument('--git-sha', required=True)
    ap.add_argument('--asset-base', required=True)
    a = ap.parse_args()

    sarus_version = '%s-S%s' % (a.version, a.build)
    fw_type = 'OFFICIAL' if a.release_type == 'stable' else 'BETA'
    tree = tempfile.mkdtemp(prefix='sarus-manifest-')
    assets = os.path.join(a.outdir, 'assets')
    os.makedirs(assets, exist_ok=True)
    asset_of = {}

    for board in sorted(os.listdir(a.fwdir)):
        bdir = os.path.join(a.fwdir, board)
        if not os.path.isdir(bdir):
            continue
        for apj, (vehicle, fwname) in VEHICLES.items():
            src = os.path.join(bdir, apj)
            if not os.path.exists(src):
                print('missing %s' % src, file=sys.stderr)
                continue
            d = os.path.join(tree, vehicle, a.release_type, board)
            os.makedirs(d, exist_ok=True)
            shutil.copy(src, os.path.join(d, apj))
            # same files ArduPilot's build server writes next to each firmware
            with open(os.path.join(d, 'git-version.txt'), 'w') as f:
                f.write('commit %s\n\nAPMVERSION: %s V%s-Sarus-%s\n' % (a.git_sha, fwname, a.version, a.build))
            with open(os.path.join(d, 'firmware-version.txt'), 'w') as f:
                f.write('%s-FIRMWARE_VERSION_TYPE_%s\n' % (a.version, fw_type))
            name = 'Sarus-%s-%s-%s.apj' % (vehicle, sarus_version, board)
            shutil.copy(src, os.path.join(assets, name))
            # key: the URL generate_manifest produces with the placeholder base below
            asset_of[PLACEHOLDER + os.path.join(tree, vehicle, a.release_type, board, apj)[len(tree):]] = name

    if not asset_of:
        sys.exit('no firmware found in %s' % a.fwdir)

    # placeholder base URL without path separators (a Windows path would be read as a regex escape)
    gen = generate_manifest.ManifestGenerator(tree, PLACEHOLDER)
    gen.run()
    manifest = json.loads(gen.json())

    entries = []
    for e in manifest['firmware']:
        name = asset_of.get(e['url'])
        if name is None:
            sys.exit('unexpected firmware path %s' % e['url'])
        e['url'] = a.asset_base.rstrip('/') + '/' + name
        entries.append(e)

    # every built file must be in the list exactly once, with the board identity ArduPilot uses
    if len(entries) != len(asset_of):
        sys.exit('manifest has %d entries for %d files' % (len(entries), len(asset_of)))
    for e in entries:
        for key in ('board_id', 'USBID', 'bootloader_str', 'platform', 'mav-type', 'mav-firmware-version'):
            if not e.get(key):
                sys.exit('entry %s has no %s' % (e['url'], key))
        if e['mav-firmware-version-type'] != fw_type:
            sys.exit('entry %s has release type %s' % (e['url'], e['mav-firmware-version-type']))

    with open(os.path.join(a.outdir, 'manifest-release.json'), 'w') as f:
        json.dump({'format-version': '1.0.0', 'firmware': entries}, f, indent=1)
    shutil.rmtree(tree)
    print('%d firmware entries, release type %s, version %s' % (len(entries), fw_type, sarus_version))


if __name__ == '__main__':
    main()
