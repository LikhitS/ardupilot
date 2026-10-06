/*
  Sarus parameter lock; see AP_SarusLock.h for what it covers.
 */
#include "AP_SarusLock.h"

#if AP_SARUS_LOCK_ENABLED

#include <AP_Math/AP_Math.h>
#include <AP_CheckFirmware/monocypher.h>
#include <GCS_MAVLink/GCS.h>
#include "monocypher_ed25519.h"
#include "AP_SarusLock_keys.h"

extern const AP_HAL::HAL &hal;

AP_SarusLock AP_SarusLock::_singleton;

namespace {

struct PublicKey {
    uint8_t key[32];
};

// keys that may unlock this firmware; the all-zero entry ends the list and is never a key
const PublicKey public_keys[] = {
#ifdef AP_SARUS_LOCK_TEST_KEY
    // test key for simulator builds only; its password is public (Tools/sarus/SARUS_LOCK.md)
    { AP_SARUS_LOCK_TEST_KEY_BYTES },
#endif
    AP_SARUS_LOCK_OWNER_KEYS
    { {0} },
};

const uint8_t num_public_keys = ARRAY_SIZE(public_keys) - 1;

bool key_is_zero(const PublicKey &k)
{
    for (const uint8_t b : k.key) {
        if (b != 0) {
            return false;
        }
    }
    return true;
}

} // namespace

bool AP_SarusLock::active() const
{
    for (uint8_t i = 0; i < num_public_keys; i++) {
        if (!key_is_zero(public_keys[i])) {
            return true;
        }
    }
    return false;
}

void AP_SarusLock::expire_unlock()
{
    // an unlock follows its station's link, but never ends in flight
    if (unlocked &&
        !hal.util->get_soft_armed() &&
        AP_HAL::millis() - unlocked_last_seen_ms > LINK_TIMEOUT_MS) {
        unlocked = false;
        GCS_SEND_TEXT(MAV_SEVERITY_INFO, "Sarus: link lost, parameters locked");
    }
}

bool AP_SarusLock::unlocked_for(mavlink_channel_t chan, uint8_t sysid)
{
    expire_unlock();
    return unlocked && unlocked_chan == chan && unlocked_sysid == sysid;
}

uint8_t AP_SarusLock::status_flags(mavlink_channel_t chan, uint8_t sysid)
{
    uint8_t flags = 0;
    if (active()) {
        flags |= FLAG_ACTIVE;
    }
    if (unlocked_for(chan, sysid)) {
        flags |= FLAG_UNLOCKED_BY_YOU;
    }
    if (unlocked) {
        flags |= FLAG_UNLOCKED;
    }
    return flags;
}

void AP_SarusLock::note_traffic(mavlink_channel_t chan, uint8_t sysid)
{
    if (!unlocked || chan != unlocked_chan || sysid != unlocked_sysid) {
        return;
    }
    WITH_SEMAPHORE(sem);
    // the first packet after a long silence must not revive the unlock, so check before refreshing
    expire_unlock();
    if (unlocked) {
        unlocked_last_seen_ms = AP_HAL::millis();
    }
}

bool AP_SarusLock::change_allowed(mavlink_channel_t chan, uint8_t sysid)
{
    if (!active()) {
        return true;
    }
    WITH_SEMAPHORE(sem);
    return unlocked_for(chan, sysid);
}

bool AP_SarusLock::command_allowed(uint16_t command, float param1, mavlink_channel_t chan, uint8_t sysid)
{
    switch (command) {
    case MAV_CMD_PREFLIGHT_CALIBRATION:
    case MAV_CMD_PREFLIGHT_SET_SENSOR_OFFSETS:
    case MAV_CMD_PREFLIGHT_UAVCAN:
    case MAV_CMD_PREFLIGHT_STORAGE:
    case MAV_CMD_DO_START_MAG_CAL:
    case MAV_CMD_DO_ACCEPT_MAG_CAL:
    case MAV_CMD_FIXED_MAG_CAL:
    case MAV_CMD_FIXED_MAG_CAL_FIELD:
    case MAV_CMD_FIXED_MAG_CAL_YAW:
    case MAV_CMD_ACCELCAL_VEHICLE_POS:
    case MAV_CMD_FLASH_BOOTLOADER:
    case MAV_CMD_STORAGE_FORMAT:
    case MAV_CMD_START_RX_PAIR:
    case MAV_CMD_SCRIPTING:
        break;
    case MAV_CMD_PREFLIGHT_REBOOT_SHUTDOWN:
        // a plain reboot or shutdown stays open; the bootloader and debug actions do not
        if (is_zero(param1) || is_equal(param1, 1.0f) || is_equal(param1, 2.0f)) {
            return true;
        }
        break;
    default:
        return true;
    }
    if (change_allowed(chan, sysid)) {
        return true;
    }
    notify_denied("setup command");
    return false;
}

void AP_SarusLock::notify_denied(const char *what)
{
    const uint32_t now = AP_HAL::millis();
    if (now - last_denied_text_ms < 2000) {
        return;
    }
    last_denied_text_ms = now;
    GCS_SEND_TEXT(MAV_SEVERITY_WARNING, "Sarus: locked, %s refused", what);
}

void AP_SarusLock::make_nonce()
{
    // hash whatever randomness the board has with values that differ per board and per boot
    struct PACKED {
        uint8_t rnd[32];
        uint64_t time_us;
        uint8_t unique_id[12];
        uint32_t extra[4];
    } seed {};
    if (!hal.util->get_random_vals(seed.rnd, sizeof(seed.rnd))) {
        for (uint8_t i = 0; i < sizeof(seed.rnd); i += 2) {
            const uint16_t r = get_random16();
            seed.rnd[i] = r & 0xFF;
            seed.rnd[i+1] = r >> 8;
        }
    }
    uint8_t uid_len = sizeof(seed.unique_id);
    hal.util->get_system_id_unformatted(seed.unique_id, uid_len);
    seed.time_us = AP_HAL::micros64();
    for (uint32_t &e : seed.extra) {
        e = (uint32_t(get_random16()) << 16) | get_random16();
    }
    crypto_blake2b_general(nonce, sizeof(nonce), nullptr, 0, (const uint8_t *)&seed, sizeof(seed));
    crypto_wipe(&seed, sizeof(seed));
}

bool AP_SarusLock::signature_ok(const SignedBlock &block, const uint8_t sig[SIG_LEN]) const
{
    for (uint8_t i = 0; i < num_public_keys; i++) {
        if (key_is_zero(public_keys[i])) {
            continue;
        }
        if (crypto_ed25519_check(sig, public_keys[i].key, (const uint8_t *)&block, sizeof(block)) == 0) {
            return true;
        }
    }
    return false;
}

void AP_SarusLock::send_reply(mavlink_channel_t chan, const mavlink_secure_command_t &pkt, MAV_RESULT result,
                              bool with_nonce, uint8_t gcs_sysid)
{
    mavlink_secure_command_reply_t reply {};
    reply.sequence = pkt.sequence;
    reply.operation = pkt.operation;
    reply.result = result;
    reply.data[0] = PROTOCOL_VERSION;
    reply.data[1] = status_flags(chan, gcs_sysid);
    reply.data[2] = num_public_keys;
    reply.data_length = 3;
    if (with_nonce) {
        memcpy(&reply.data[3], nonce, NONCE_LEN);
        reply.data_length += NONCE_LEN;
    }
    mavlink_msg_secure_command_reply_send_struct(chan, &reply);
}

bool AP_SarusLock::handle_secure_command(mavlink_channel_t chan, const mavlink_message_t &msg)
{
    if (msg.msgid != MAVLINK_MSG_ID_SECURE_COMMAND) {
        return false;
    }
    mavlink_secure_command_t pkt;
    mavlink_msg_secure_command_decode(&msg, &pkt);
    const Op op = Op(pkt.operation);
    if (op != Op::STATUS && op != Op::GET_NONCE && op != Op::UNLOCK && op != Op::LOCK) {
        // not ours; ArduPilot's own secure commands use small operation numbers
        return false;
    }
    if (pkt.target_system != mavlink_system.sysid) {
        return true;
    }

    WITH_SEMAPHORE(sem);

    switch (op) {
    case Op::STATUS:
        send_reply(chan, pkt, MAV_RESULT_ACCEPTED, false, msg.sysid);
        break;

    case Op::GET_NONCE:
        make_nonce();
        nonce_valid = true;
        nonce_ms = AP_HAL::millis();
        nonce_chan = chan;
        nonce_sysid = msg.sysid;
        send_reply(chan, pkt, MAV_RESULT_ACCEPTED, true, msg.sysid);
        break;

    case Op::UNLOCK: {
        if (!active()) {
            send_reply(chan, pkt, MAV_RESULT_ACCEPTED, false, msg.sysid);
            break;
        }
        if (hal.util->get_soft_armed()) {
            // the check is too slow for the main loop in flight; unlock before arming
            send_reply(chan, pkt, MAV_RESULT_TEMPORARILY_REJECTED, false, msg.sysid);
            break;
        }
        // a nonce is good for one attempt, from the station that asked for it
        const bool nonce_fresh = nonce_valid &&
            nonce_chan == chan && nonce_sysid == msg.sysid &&
            AP_HAL::millis() - nonce_ms < NONCE_TIMEOUT_MS;
        nonce_valid = false;
        if (!nonce_fresh ||
            pkt.data_length != NONCE_LEN || pkt.sig_length != SIG_LEN ||
            memcmp(pkt.data, nonce, NONCE_LEN) != 0) {
            GCS_SEND_TEXT(MAV_SEVERITY_WARNING, "Sarus: unlock refused (stale request)");
            send_reply(chan, pkt, MAV_RESULT_DENIED, false, msg.sysid);
            break;
        }
        SignedBlock block {};
        memcpy(block.magic, "SARUS-LOCK1", 12);
        block.operation = uint32_t(Op::UNLOCK);
        block.target_system = pkt.target_system;
        block.target_component = pkt.target_component;
        block.gcs_sysid = msg.sysid;
        memcpy(block.nonce, nonce, NONCE_LEN);
        if (!signature_ok(block, &pkt.data[NONCE_LEN])) {
            GCS_SEND_TEXT(MAV_SEVERITY_WARNING, "Sarus: unlock refused (wrong key)");
            send_reply(chan, pkt, MAV_RESULT_DENIED, false, msg.sysid);
            break;
        }
        unlocked = true;
        unlocked_chan = chan;
        unlocked_sysid = msg.sysid;
        unlocked_last_seen_ms = AP_HAL::millis();
        GCS_SEND_TEXT(MAV_SEVERITY_INFO, "Sarus: parameters unlocked");
        send_reply(chan, pkt, MAV_RESULT_ACCEPTED, false, msg.sysid);
        break;
    }

    case Op::LOCK:
        if (unlocked) {
            unlocked = false;
            GCS_SEND_TEXT(MAV_SEVERITY_INFO, "Sarus: parameters locked");
        }
        send_reply(chan, pkt, MAV_RESULT_ACCEPTED, false, msg.sysid);
        break;
    }
    return true;
}

namespace AP {
AP_SarusLock &sarus_lock()
{
    return *AP_SarusLock::get_singleton();
}
};

#endif // AP_SARUS_LOCK_ENABLED
