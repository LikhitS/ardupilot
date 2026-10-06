#!/usr/bin/env python3
"""
End-to-end test of the Sarus parameter lock against a running SITL built with
--define=AP_SARUS_LOCK_TEST_KEY=1 (the simulator test key, password in lock_keys.py).

  lock_sitl_test.py [--main tcp:127.0.0.1:5760] [--second tcp:127.0.0.1:5762] [--reboot]

It plays the parts of the owner's ground station, a station without the password, and a
station on a second link, and checks what each may do. Exit code 0 means every check passed.
"""
import argparse
import os
import struct
import sys
import time

from pymavlink import mavutil
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import lock_keys  # noqa: E402

OP_STATUS, OP_GET_NONCE, OP_UNLOCK, OP_LOCK = 0x53524C01, 0x53524C02, 0x53524C03, 0x53524C04
FLAG_ACTIVE, FLAG_UNLOCKED, FLAG_UNLOCKED_BY_YOU = 1, 2, 4
PARAM = 'LOG_DISARMED'
FTP_CREATE, FTP_REMOVE, FTP_NACK, FTP_ACK, FTP_ERR_PROTECTED = 6, 8, 129, 128, 9

failures = []
seq = [0]


def check(cond, what):
    print('%s  %s' % ('PASS' if cond else 'FAIL', what))
    if not cond:
        failures.append(what)


def connect(url, sysid):
    m = mavutil.mavlink_connection(url, source_system=sysid, source_component=190, autoreconnect=True)
    m.wait_heartbeat(timeout=60)
    return m


def drain(m):
    while m.recv_match(blocking=False) is not None:
        pass


def secure(m, op, data=b'', sig=b''):
    seq[0] += 1
    payload = list(data + sig) + [0] * (220 - len(data) - len(sig))
    drain(m)
    m.mav.secure_command_send(m.target_system, m.target_component, seq[0], op, len(data), len(sig), payload)
    end = time.time() + 5
    while time.time() < end:
        r = m.recv_match(type='SECURE_COMMAND_REPLY', blocking=True, timeout=1)
        if r is not None and r.sequence == seq[0]:
            return r
    return None


def status(m):
    r = secure(m, OP_STATUS)
    return None if r is None else r.data[1]


def sign_unlock(m, key, nonce, gcs_sysid=None):
    block = b'SARUS-LOCK1\x00' + struct.pack('<IBBB', OP_UNLOCK, m.target_system, m.target_component,
                                             m.mav.srcSystem if gcs_sysid is None else gcs_sysid) + bytes(nonce)
    return key.sign(block)


def unlock(m, key, tamper_nonce=False, gcs_sysid=None):
    r = secure(m, OP_GET_NONCE)
    if r is None:
        return None
    nonce = bytes(r.data[3:19])
    signed = bytearray(nonce)
    if tamper_nonce:
        signed[0] ^= 1
    sig = sign_unlock(m, key, bytes(signed), gcs_sysid)
    r = secure(m, OP_UNLOCK, nonce, sig)
    return None if r is None else r.result


def get_param(m, name):
    drain(m)
    m.mav.param_request_read_send(m.target_system, m.target_component, name.encode(), -1)
    r = m.recv_match(type='PARAM_VALUE', blocking=True, timeout=5)
    while r is not None and r.param_id != name:
        r = m.recv_match(type='PARAM_VALUE', blocking=True, timeout=5)
    return None if r is None else r.param_value


def set_param(m, name, value):
    """returns (value the aircraft reports back, any Sarus STATUSTEXT seen)"""
    drain(m)
    m.mav.param_set_send(m.target_system, m.target_component, name.encode(), value,
                         mavutil.mavlink.MAV_PARAM_TYPE_REAL32)
    got, text, end = None, '', time.time() + 4
    while time.time() < end:
        r = m.recv_match(type=['PARAM_VALUE', 'STATUSTEXT'], blocking=True, timeout=1)
        if r is None:
            continue
        if r.get_type() == 'STATUSTEXT' and 'Sarus' in r.text:
            text = r.text
        if r.get_type() == 'PARAM_VALUE' and r.param_id == name and got is None:
            got = r.param_value
    return got, text


def command(m, cmd, p1=0, p2=0, p3=0, p4=0, p5=0, p6=0, p7=0):
    drain(m)
    m.mav.command_long_send(m.target_system, m.target_component, cmd, 0, p1, p2, p3, p4, p5, p6, p7)
    end = time.time() + 5
    while time.time() < end:
        r = m.recv_match(type='COMMAND_ACK', blocking=True, timeout=1)
        if r is not None and r.command == cmd:
            return r.result
    return None


def ftp(m, opcode, path):
    seq[0] += 1
    data = path.encode()
    hdr = struct.pack('<HBBBBBBI', seq[0] & 0xFFFF, 0, opcode, len(data), 0, 0, 0, 0)
    payload = list(hdr + data) + [0] * (251 - len(hdr) - len(data))
    drain(m)
    m.mav.file_transfer_protocol_send(0, m.target_system, m.target_component, payload)
    end = time.time() + 5
    while time.time() < end:
        r = m.recv_match(type='FILE_TRANSFER_PROTOCOL', blocking=True, timeout=1)
        if r is None:
            continue
        p = bytes(r.payload)
        if struct.unpack('<H', p[0:2])[0] == (seq[0] + 1) & 0xFFFF or p[5] == opcode:
            return p[3], (p[12] if p[4] else None)
    return None, None


def inactive(args):
    """firmware without any key must behave like ArduPilot"""
    MAV = mavutil.mavlink
    m = connect(args.main, 255)
    f = status(m)
    check(f is not None and not f & FLAG_ACTIVE, 'aircraft reports no lock (flags %s)' % f)
    original = get_param(m, PARAM)
    target = 0.0 if original else 1.0
    got, _ = set_param(m, PARAM, target)
    check(got == target and get_param(m, PARAM) == target, 'parameter write applied without unlocking')
    set_param(m, PARAM, original)
    check(get_param(m, PARAM) == original, 'parameter restored')
    check(command(m, MAV.MAV_CMD_PREFLIGHT_CALIBRATION, 0, 0, 1) != MAV.MAV_RESULT_DENIED,
          'calibration command not refused by the lock')
    op, err = ftp(m, FTP_CREATE, 'sarus_lock_test.txt')
    check(op == FTP_ACK, 'FTP file write allowed (op %s err %s)' % (op, err))
    ftp(m, 2, '')
    ftp(m, FTP_REMOVE, 'sarus_lock_test.txt')
    print('\n%s: %d failure(s)' % ('PASS' if not failures else 'FAIL', len(failures)))
    return 1 if failures else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--main', default='tcp:127.0.0.1:5760')
    ap.add_argument('--second', default='tcp:127.0.0.1:5762')
    ap.add_argument('--reboot', action='store_true', help='also check that a reboot locks again')
    ap.add_argument('--expect-inactive', action='store_true',
                    help='firmware built without keys: check that nothing is locked')
    args = ap.parse_args()
    if args.expect_inactive:
        return inactive(args)

    key = lock_keys.derive_private(lock_keys.TEST_PASSWORD, lock_keys.TEST_SALT)
    stranger = Ed25519PrivateKey.generate()
    MAV = mavutil.mavlink

    m = connect(args.main, 255)
    f = status(m)
    check(f is not None and f & FLAG_ACTIVE and not f & FLAG_UNLOCKED, 'aircraft reports the lock active and locked')
    original = get_param(m, PARAM)
    check(original is not None, 'parameters can be read while locked (%s=%s)' % (PARAM, original))
    target = 0.0 if original else 1.0

    got, text = set_param(m, PARAM, target)
    check(got == original, 'locked: parameter write refused, aircraft sends back its own value (%s)' % got)
    check('locked' in text, 'locked: pilot is told why (%r)' % text)
    check(get_param(m, PARAM) == original, 'locked: value unchanged on re-read')

    check(command(m, MAV.MAV_CMD_PREFLIGHT_CALIBRATION, 0, 0, 1) == MAV.MAV_RESULT_DENIED,
          'locked: calibration command denied')
    check(command(m, MAV.MAV_CMD_PREFLIGHT_STORAGE, 2) == MAV.MAV_RESULT_DENIED,
          'locked: parameter reset denied')
    check(command(m, MAV.MAV_CMD_PREFLIGHT_REBOOT_SHUTDOWN, 3) == MAV.MAV_RESULT_DENIED,
          'locked: reboot into bootloader denied')
    check(command(m, MAV.MAV_CMD_REQUEST_MESSAGE, MAV.MAVLINK_MSG_ID_AUTOPILOT_VERSION) == MAV.MAV_RESULT_ACCEPTED,
          'locked: ordinary commands still work')
    op, err = ftp(m, FTP_CREATE, 'sarus_lock_test.txt')
    check(op == FTP_NACK and err == FTP_ERR_PROTECTED, 'locked: FTP file write refused (op %s err %s)' % (op, err))

    check(unlock(m, stranger) == MAV.MAV_RESULT_DENIED, 'unlock with a wrong key refused')
    check(unlock(m, key, tamper_nonce=True) == MAV.MAV_RESULT_DENIED, 'unlock signed over the wrong nonce refused')
    r = secure(m, OP_UNLOCK, bytes(16), sign_unlock(m, key, bytes(16)))
    check(r is not None and r.result == MAV.MAV_RESULT_DENIED, 'unlock without asking for a nonce refused')
    check(unlock(m, key, gcs_sysid=254) == MAV.MAV_RESULT_DENIED, 'unlock signed for another station refused')
    check(get_param(m, PARAM) == original and set_param(m, PARAM, target)[0] == original,
          'still locked after the refused attempts')

    check(unlock(m, key) == MAV.MAV_RESULT_ACCEPTED, 'unlock with the right key accepted')
    f = status(m)
    check(f is not None and f & FLAG_UNLOCKED_BY_YOU, 'status shows unlocked by this station')
    got, _ = set_param(m, PARAM, target)
    check(got == target and get_param(m, PARAM) == target, 'unlocked: parameter write applied (%s)' % got)
    op, err = ftp(m, FTP_CREATE, 'sarus_lock_test.txt')
    check(op == FTP_ACK, 'unlocked: FTP file write allowed (op %s err %s)' % (op, err))
    ftp(m, 2, '')  # ResetSessions closes the file
    ftp(m, FTP_REMOVE, 'sarus_lock_test.txt')

    # another station on the same link, and the same station id on another link, stay locked
    m.mav.srcSystem = 254
    got, _ = set_param(m, PARAM, original)
    check(got == target, 'unlock does not extend to another station on the same link')
    m.mav.srcSystem = 255
    try:
        m2 = connect(args.second, 255)
        got, _ = set_param(m2, PARAM, original)
        check(got == target, 'unlock does not extend to the same station id on another link')
        m2.close()
    except Exception as e:  # a SITL without a second MAVLink port
        print('SKIP  second link: %s' % e)

    r = secure(m, OP_LOCK)
    check(r is not None and r.result == MAV.MAV_RESULT_ACCEPTED, 'lock request accepted')
    check(set_param(m, PARAM, original)[0] == target, 'locked again: write refused')

    # arming: unlocking is refused in flight, an unlock made on the ground survives arming
    check(unlock(m, key) == MAV.MAV_RESULT_ACCEPTED, 'unlocked again on the ground')
    armed = command(m, MAV.MAV_CMD_COMPONENT_ARM_DISARM, 1, 21196) == MAV.MAV_RESULT_ACCEPTED
    check(armed, 'force-armed for the in-flight checks')
    if armed:
        got, _ = set_param(m, PARAM, original)
        check(got == original, 'armed: unlock made on the ground still allows tuning')
        secure(m, OP_LOCK)
        check(unlock(m, key) == MAV.MAV_RESULT_TEMPORARILY_REJECTED, 'armed: new unlock refused until disarmed')
        check(command(m, MAV.MAV_CMD_COMPONENT_ARM_DISARM, 0, 21196) == MAV.MAV_RESULT_ACCEPTED, 'disarmed')

    # silence on the unlocking link ends the unlock
    check(unlock(m, key) == MAV.MAV_RESULT_ACCEPTED, 'unlocked before the silence test')
    current = get_param(m, PARAM)
    print('....  staying silent for 12 s')
    time.sleep(12)
    got, text = set_param(m, PARAM, 0.0 if current else 1.0)
    check(got == current, 'after 12 s of silence the first write is refused (%r)' % text)
    f = status(m)
    check(f is not None and not f & FLAG_UNLOCKED, 'status shows locked after the silence')

    # put the parameter back
    check(unlock(m, key) == MAV.MAV_RESULT_ACCEPTED, 'unlocked to restore')
    set_param(m, PARAM, original)
    check(get_param(m, PARAM) == original, 'parameter restored to %s' % original)

    if args.reboot:
        check(command(m, MAV.MAV_CMD_PREFLIGHT_REBOOT_SHUTDOWN, 1) == MAV.MAV_RESULT_ACCEPTED, 'plain reboot allowed')
        time.sleep(5)
        m.close()
        m = connect(args.main, 255)
        f = status(m)
        check(f is not None and f & FLAG_ACTIVE and not f & FLAG_UNLOCKED, 'locked again after reboot')
    else:
        secure(m, OP_LOCK)

    print('\n%s: %d failure(s)' % ('PASS' if not failures else 'FAIL', len(failures)))
    for f in failures:
        print('  - ' + f)
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
