<!-- lint-allow: unlock -->
# Sarus parameter lock

Sarus firmware refuses setup changes from a ground station until that station proves it knows the owner's admin password. Anyone can still connect, watch telemetry, read every parameter, plan and fly. What needs the password is changing how the aircraft is set up.

## What is locked

While locked, the aircraft refuses parameter writes (it sends back the value it keeps, and on the 4.7 line also a PARAM_ERROR), parameter resets, every calibration, sensor offset changes, reboot into the bootloader, bootloader flashing, SD card formatting, receiver pairing, Lua scripting commands, OSD parameter changes, MAVLink signing setup, and any file write, rename or delete over MAVLink FTP. FTP writes are included because a parameter file written to `@PARAM` loads parameters, and a script copied to `APM/scripts` can set them. A plain reboot or shutdown stays open.

Each refusal sends the message "Sarus: locked, ... refused" at most once every two seconds.

## How unlocking works

The aircraft never holds anything secret. It holds Ed25519 public keys, compiled in from `sarus-lock-keys.json`. The private key behind each one is derived from the admin password with scrypt (N 65536, r 8, p 1, a 16-byte salt kept next to the public key), so it exists only inside Sarus Operation Planner while someone who typed the password has it unlocked.

The exchange uses the SECURE_COMMAND message with operation numbers starting 0x53524C ("SRL"), well clear of ArduPilot's own secure commands:

1. The station sends GET_NONCE. The aircraft answers with 16 fresh random bytes and its lock state.
2. The station signs a 35-byte block, the text "SARUS-LOCK1" with a terminating zero, the operation number, the aircraft's system and component ids, the station's own system id and the nonce. It sends UNLOCK with the nonce and the 64-byte signature.
3. The aircraft checks the signature against each public key. A nonce is good for one attempt, for 30 seconds, and only from the station and link that asked for it, so a recorded unlock cannot be replayed.

The unlock belongs to that station on that link. Another station, or the same station id on a different radio, is still refused. It ends on reboot, on a LOCK request, or after 10 seconds of silence from that station while disarmed. Nothing ends it in flight, but a new unlock is refused while armed, because the signature check costs a few milliseconds of main-loop time. Unlock on the ground before take-off if you intend to tune in the air.

## Keys

`lock_keys.py setup` asks for the password twice without echoing it, adds a public key and salt to `sarus-lock-keys.json` and rewrites `libraries/AP_SarusLock/AP_SarusLock_keys.h`. Both files are safe to publish. Several keys may be listed, for example a second password kept in a safe as a spare, and any one of them unlocks.

Firmware built without any owner key leaves the lock off and behaves exactly like ArduPilot. The release job refuses to publish firmware without an owner key.

Simulator builds made with `--define=AP_SARUS_LOCK_TEST_KEY=1` also accept a test key whose password, `sarus-sitl-test`, is printed in `lock_keys.py`. Never use that option for a board.

## Limits

The lock protects the aircraft from other ground stations. It cannot stop someone with the flight controller on their bench from flashing different firmware through the bootloader over USB. Closing that gap needs ArduPilot's secure bootloader with a Sarus signing key, which is a separate step. DroneCAN peripheral settings changed through CAN forwarding are not covered either.

If every password is lost, no station can unlock the aircraft again until it is flashed with firmware that carries a new key.

## Tests

`tests/ed25519_check.cpp` checks the signature code against RFC 8032 vectors and against signatures made by Python's cryptography package. `tests/lock_sitl_test.py` runs against a simulator built with the test key and checks each refusal, wrong keys, replays, other stations, arming, the silence timeout and reboot. With `--expect-inactive` it checks that a build without keys locks nothing. The firmware CI runs all three on every push.
