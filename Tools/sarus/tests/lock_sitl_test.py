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

os.environ.setdefault('MAVLINK20', '1')  # SECURE_COMMAND and SETUP_SIGNING do not exist in MAVLink 1

from pymavlink import mavutil
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import lock_keys  # noqa: E402

OP_STATUS, OP_GET_NONCE, OP_UNLOCK, OP_LOCK = 0x53524C01, 0x53524C02, 0x53524C03, 0x53524C04
FLAG_ACTIVE, FLAG_UNLOCKED, FLAG_UNLOCKED_BY_YOU = 1, 2, 4
PARAM = 'LOG_DISARMED'
FTP_CREATE, FTP_REMOVE, FTP_NACK, FTP_ACK, FTP_ERR_PROTECTED = 6, 8, 129, 128, 9
FTP_WRITE, FTP_MKDIR, FTP_RMDIR, FTP_OPEN_WO, FTP_TRUNCATE, FTP_RENAME = 7, 9, 10, 11, 12, 13
AUX_SAVE_TRIM, AUX_SAVE_WP, AUX_MAG_CAL = 5, 7, 171
MAV_CMD_DO_AUX_FUNCTION = 218
UNLOCK_GAP = 0.3  # the firmware refuses an UNLOCK within 250 ms of a failed signature check
NONCE_TIMEOUT = 30  # the firmware's nonce lifetime in seconds

failures = []
total = [0]
seq = [0]


def check(cond, what):
    total[0] += 1
    print('%s  %s' % ('PASS' if cond else 'FAIL', what))
    if not cond:
        failures.append(what)


def connect(url, sysid):
    m = mavutil.mavlink_connection(url, source_system=sysid, source_component=190, autoreconnect=True)
    if m.wait_heartbeat(timeout=60) is None:
        raise RuntimeError('no heartbeat from %s' % url)
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


def get_nonce(m):
    r = secure(m, OP_GET_NONCE)
    return None if r is None else bytes(r.data[3:19])


def send_unlock(m, nonce, sig):
    """UNLOCK after the pause the firmware wants since a failed check; returns the reply or None"""
    time.sleep(UNLOCK_GAP)
    return secure(m, OP_UNLOCK, nonce, sig)


def unlock(m, key, tamper_nonce=False, gcs_sysid=None):
    return unlock_attempt(m, key, tamper_nonce, gcs_sysid)[0]


def unlock_attempt(m, key, tamper_nonce=False, gcs_sysid=None):
    """-> (result or None, nonce, signature), so a test can replay what was sent"""
    r = secure(m, OP_GET_NONCE)
    if r is None:
        return None, b'', b''
    nonce = bytes(r.data[3:19])
    signed = bytearray(nonce)
    if tamper_nonce:
        signed[0] ^= 1
    sig = sign_unlock(m, key, bytes(signed), gcs_sysid)
    r = send_unlock(m, nonce, sig)
    return (None if r is None else r.result), nonce, sig


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


def command_int(m, cmd, p1=0, p2=0, p3=0, p4=0, x=0, y=0, z=0):
    """the same as command(), sent as COMMAND_INT (the firmware gates the two paths separately)"""
    drain(m)
    m.mav.command_int_send(m.target_system, m.target_component, mavutil.mavlink.MAV_FRAME_MISSION, cmd, 0, 0,
                           p1, p2, p3, p4, x, y, z)
    end = time.time() + 5
    while time.time() < end:
        r = m.recv_match(type='COMMAND_ACK', blocking=True, timeout=1)
        if r is not None and r.command == cmd:
            return r.result
    return None


def setup_signing(m, secret):
    """sends SETUP_SIGNING; returns the Sarus STATUSTEXT seen, if any"""
    drain(m)
    m.mav.setup_signing_send(m.target_system, m.target_component, list(secret), int(time.time() * 1e5))
    text, end = '', time.time() + 3
    while time.time() < end:
        r = m.recv_match(type='STATUSTEXT', blocking=True, timeout=1)
        if r is not None and 'Sarus' in r.text:
            text = r.text
    return text


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
    print('\n%s: %d failure(s) in %d checks' % ('PASS' if not failures else 'FAIL', len(failures), total[0]))
    return 1 if failures else 0


def release(args):
    """release firmware (owner keys, no test key): locked, and the public test key must not unlock it"""
    MAV = mavutil.mavlink
    m = connect(args.main, 255)
    f = status(m)
    check(f is not None and f & FLAG_ACTIVE and not f & FLAG_UNLOCKED, 'release build reports the lock active and locked (flags %s)' % f)
    r = secure(m, OP_STATUS)
    check(r is not None and r.data[2] >= 1, 'release build holds the owner key(s) (%s)' % (r.data[2] if r else None))
    test_key = lock_keys.derive_private(lock_keys.TEST_PASSWORD, lock_keys.TEST_SALT)
    check(unlock(m, test_key) == MAV.MAV_RESULT_DENIED, 'the public simulator test key does not unlock release firmware')
    check(unlock(m, Ed25519PrivateKey.generate()) == MAV.MAV_RESULT_DENIED, 'a random key does not unlock it')
    original = get_param(m, PARAM)
    got, _ = set_param(m, PARAM, 0.0 if original else 1.0)
    check(got == original and get_param(m, PARAM) == original, 'release build refuses a parameter write')
    check(command(m, MAV.MAV_CMD_REQUEST_MESSAGE, MAV.MAVLINK_MSG_ID_AUTOPILOT_VERSION) == MAV.MAV_RESULT_ACCEPTED,
          'release build still accepts ordinary commands')
    print('\n%s: %d failure(s) in %d checks' % ('PASS' if not failures else 'FAIL', len(failures), total[0]))
    return 1 if failures else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--main', default='tcp:127.0.0.1:5760')
    ap.add_argument('--second', default='tcp:127.0.0.1:5762')
    ap.add_argument('--reboot', action='store_true', help='also check that a reboot locks again')
    ap.add_argument('--expect-inactive', action='store_true',
                    help='firmware built without keys: check that nothing is locked')
    ap.add_argument('--expect-release', action='store_true',
                    help='firmware built with owner keys only: locked, and the test key does not unlock it')
    args = ap.parse_args()
    if args.expect_inactive:
        return inactive(args)
    if args.expect_release:
        return release(args)

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
    check(command_int(m, MAV.MAV_CMD_PREFLIGHT_CALIBRATION, 0, 0, 1) == MAV.MAV_RESULT_DENIED,
          'locked: calibration command sent as COMMAND_INT denied')
    check(command_int(m, MAV.MAV_CMD_PREFLIGHT_STORAGE, 2) == MAV.MAV_RESULT_DENIED,
          'locked: parameter reset sent as COMMAND_INT denied')
    check(command_int(m, MAV.MAV_CMD_REQUEST_MESSAGE, MAV.MAVLINK_MSG_ID_AUTOPILOT_VERSION) == MAV.MAV_RESULT_ACCEPTED,
          'locked: ordinary COMMAND_INT still works')
    # aux functions write setup (trim, waypoint, compass calibration); switch position 0 so nothing runs if one slips through
    check(command(m, MAV_CMD_DO_AUX_FUNCTION, AUX_MAG_CAL, 0) == MAV.MAV_RESULT_DENIED,
          'locked: aux function MAG_CAL (171) denied')
    check(command(m, MAV_CMD_DO_AUX_FUNCTION, AUX_SAVE_TRIM, 0) == MAV.MAV_RESULT_DENIED,
          'locked: aux function SAVE_TRIM (5) denied')
    r = command(m, MAV_CMD_DO_AUX_FUNCTION, AUX_SAVE_WP, 0)
    check(r is not None and r != MAV.MAV_RESULT_DENIED, 'locked: aux function SAVE_WP (7) not refused by the lock (result %s)' % r)
    check(command_int(m, MAV_CMD_DO_AUX_FUNCTION, AUX_MAG_CAL, 0) == MAV.MAV_RESULT_DENIED,
          'locked: aux function MAG_CAL sent as COMMAND_INT denied')
    op, err = ftp(m, FTP_CREATE, 'sarus_lock_test.txt')
    check(op == FTP_NACK and err == FTP_ERR_PROTECTED, 'locked: FTP file write refused (op %s err %s)' % (op, err))
    for name, opcode, path in (('OpenFileWO', FTP_OPEN_WO, 'sarus_lock_test.txt'),
                               ('WriteFile', FTP_WRITE, ''),
                               ('TruncateFile', FTP_TRUNCATE, 'sarus_lock_test.txt'),
                               ('Rename', FTP_RENAME, 'sarus_lock_test.txt\x00sarus_lock_test2.txt'),
                               ('RemoveFile', FTP_REMOVE, 'sarus_lock_test.txt'),
                               ('CreateDirectory', FTP_MKDIR, 'sarus_lock_dir'),
                               ('RemoveDirectory', FTP_RMDIR, 'sarus_lock_dir')):
        op, err = ftp(m, opcode, path)
        check(op == FTP_NACK and err == FTP_ERR_PROTECTED, 'locked: FTP %s refused (op %s err %s)' % (name, op, err))
    # a new signing key would shut the other stations out; the aircraft must still take unsigned traffic afterwards
    time.sleep(2.2)  # the pilot message about a refusal is rate limited
    text = setup_signing(m, os.urandom(32))
    check('signing' in text, 'locked: SETUP_SIGNING refused, pilot is told why (%r)' % text)
    f = status(m)
    check(f is not None and get_param(m, PARAM) == original,
          'locked: after SETUP_SIGNING the aircraft still accepts unsigned messages (signing key unchanged)')

    check(unlock(m, stranger) == MAV.MAV_RESULT_DENIED, 'unlock with a wrong key refused')
    check(unlock(m, key, tamper_nonce=True) == MAV.MAV_RESULT_DENIED, 'unlock signed over the wrong nonce refused')
    r = send_unlock(m, bytes(16), sign_unlock(m, key, bytes(16)))
    check(r is not None and r.result == MAV.MAV_RESULT_DENIED, 'unlock without asking for a nonce refused')
    check(unlock(m, key, gcs_sysid=254) == MAV.MAV_RESULT_DENIED, 'unlock signed for another station refused')
    check(get_param(m, PARAM) == original and set_param(m, PARAM, target)[0] == original,
          'still locked after the refused attempts')

    # a nonce is good once, for the station that asked, until another is asked for, and for 30 s
    n1 = get_nonce(m)
    n2 = get_nonce(m)
    check(n1 is not None and n2 is not None and n1 != n2, 'a new nonce request gives a different nonce')
    if n1 is not None:
        r = send_unlock(m, n1, sign_unlock(m, key, n1))
        check(r is not None and r.result == MAV.MAV_RESULT_DENIED, 'a new nonce request voids the previous nonce')
    n1 = get_nonce(m)
    n2 = get_nonce(m)
    if n2 is not None:
        r = send_unlock(m, n2, sign_unlock(m, key, n2))
        check(r is not None and r.result == MAV.MAV_RESULT_ACCEPTED, 'the newest nonce still unlocks')
    secure(m, OP_LOCK)
    check(set_param(m, PARAM, target)[0] == original, 'locked after the nonce checks')

    n = get_nonce(m)
    m.mav.srcSystem = 254
    r = send_unlock(m, n, sign_unlock(m, key, n, 254)) if n is not None else None
    m.mav.srcSystem = 255
    check(r is not None and r.result == MAV.MAV_RESULT_DENIED,
          'a nonce used by another station id than the one that asked is refused')
    f = status(m)
    check(f is not None and not f & FLAG_UNLOCKED and set_param(m, PARAM, target)[0] == original,
          'still locked after the other-station nonce use')

    n = get_nonce(m)
    print('....  waiting %d s for the nonce to expire' % (NONCE_TIMEOUT + 1))
    time.sleep(NONCE_TIMEOUT + 1)
    r = send_unlock(m, n, sign_unlock(m, key, n)) if n is not None else None
    check(r is not None and r.result == MAV.MAV_RESULT_DENIED, 'a nonce older than %d s is refused' % NONCE_TIMEOUT)
    f = status(m)
    check(f is not None and not f & FLAG_UNLOCKED, 'still locked after the expired nonce')

    res, used_nonce, used_sig = unlock_attempt(m, key)
    check(res == MAV.MAV_RESULT_ACCEPTED, 'unlock with the right key accepted')
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
    except Exception as e:
        # the second link is part of the check, so a missing one is a failure, not a skip
        check(False, 'unlock does not extend to the same station id on another link (second link failed: %s)' % e)

    r = secure(m, OP_LOCK)
    check(r is not None and r.result == MAV.MAV_RESULT_ACCEPTED, 'lock request accepted')
    check(set_param(m, PARAM, original)[0] == target, 'locked again: write refused')

    # replay: the nonce and signature of the accepted unlock above must be dead after the lock
    r = send_unlock(m, used_nonce, used_sig)
    check(r is not None and r.result == MAV.MAV_RESULT_DENIED, 'replay of an accepted unlock after a lock refused')
    f = status(m)
    check(f is not None and not f & FLAG_UNLOCKED and set_param(m, PARAM, original)[0] == target,
          'still locked after the replay')
    # and replayed against a fresh nonce request
    n = get_nonce(m)
    r = send_unlock(m, n, used_sig) if n is not None else None
    check(r is not None and r.result == MAV.MAV_RESULT_DENIED, 'old signature with a new nonce refused')

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

    # the unlock lasts while the station keeps talking, well past the 10 s silence limit
    check(unlock(m, key) == MAV.MAV_RESULT_ACCEPTED, 'unlocked before the traffic test')
    print('....  keeping traffic going for 13 s')
    for _ in range(13):
        get_param(m, PARAM)
        time.sleep(1)
    f = status(m)
    check(f is not None and f & FLAG_UNLOCKED_BY_YOU, 'still unlocked after 13 s of traffic')
    cur = get_param(m, PARAM)
    flipped = 0.0 if cur else 1.0
    got, _ = set_param(m, PARAM, flipped)
    check(got == flipped and get_param(m, PARAM) == flipped, 'unlocked: write still applied after 13 s of traffic')

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

    print('\n%s: %d failure(s) in %d checks' % ('PASS' if not failures else 'FAIL', len(failures), total[0]))
    for f in failures:
        print('  - ' + f)
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
