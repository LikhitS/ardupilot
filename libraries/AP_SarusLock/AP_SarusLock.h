/*
  Sarus parameter lock.

  Viewing and flying stay open to every ground station. Changing the aircraft's setup does
  not: parameter writes, calibrations, parameter resets, file writes over MAVLink FTP,
  signing setup and reboot into the bootloader are refused until a ground station unlocks
  the aircraft. To unlock, the station asks for a one-time nonce and returns it signed with
  the owner's Ed25519 key, which Sarus Operation Planner derives from the admin password.
  The aircraft holds only public keys, so reading them out of the firmware gives nothing.

  An unlock belongs to the link and ground station that made it. It ends on reboot, on a
  LOCK request from that station, or when that station has been silent for LINK_TIMEOUT_MS
  while disarmed. Unlocking is only accepted while disarmed, because the signature check
  takes several milliseconds of main-loop time, and after a failed check the next one waits
  RETRY_MS.

  Without MAVLink signing, a station is known only by its link and system id. Another
  station on the same link using the same system id shares the unlock, and a station that
  can pose as an aircraft to the owner's ground station could relay the owner's signature.
  Turn on MAVLink signing on links that others can reach.

  Paths that do not come from a ground station (FrSky and CRSF parameter menus on the
  transmitter) may change parameters only while some station holds an unlock.

  Firmware built without any key leaves the lock off and behaves like ArduPilot.
 */
#pragma once

#include "AP_SarusLock_config.h"

#if AP_SARUS_LOCK_ENABLED

#include <AP_Common/AP_Common.h>
#include <AP_HAL/AP_HAL.h>
#include <AP_HAL/Semaphores.h>
#include <GCS_MAVLink/GCS_MAVLink.h>

class AP_SarusLock {
public:
    AP_SarusLock() {}

    CLASS_NO_COPY(AP_SarusLock);

    // operations carried in SECURE_COMMAND.operation; 0x53524C = "SRL"
    enum class Op : uint32_t {
        STATUS    = 0x53524C01,
        GET_NONCE = 0x53524C02,
        UNLOCK    = 0x53524C03,
        LOCK      = 0x53524C04,
    };

    // bits in byte 1 of a STATUS or GET_NONCE reply
    enum StatusFlags : uint8_t {
        FLAG_ACTIVE          = 1U << 0, // firmware holds at least one key, so the lock applies
        FLAG_UNLOCKED        = 1U << 1, // some station has unlocked
        FLAG_UNLOCKED_BY_YOU = 1U << 2, // the asking station, on this link, has unlocked
    };

    static constexpr uint8_t PROTOCOL_VERSION = 1;
    static constexpr uint8_t NONCE_LEN = 16;
    static constexpr uint8_t SIG_LEN = 64;
    static constexpr uint32_t NONCE_TIMEOUT_MS = 30000;
    static constexpr uint32_t LINK_TIMEOUT_MS = 10000;
    static constexpr uint32_t RETRY_MS = 250;

    // true if a SECURE_COMMAND carried a Sarus operation and was handled here
    bool handle_secure_command(mavlink_channel_t chan, const mavlink_message_t &msg);

    // every packet received, so an unlock can follow its station's link
    void note_traffic(mavlink_channel_t chan, uint8_t sysid);

    // may this station change parameters or files now?
    bool change_allowed(mavlink_channel_t chan, uint8_t sysid);

    // may this station run this command now? Only setup commands are restricted.
    bool command_allowed(uint16_t command, float param1, mavlink_channel_t chan, uint8_t sysid);

    // may a path with no ground station behind it (transmitter menus) change parameters now?
    bool local_change_allowed();

    // tell the pilot a change was refused, at most once every two seconds
    void notify_denied(const char *what);

    // true when the firmware holds a key, so the lock is in force
    bool active() const;

    static AP_SarusLock *get_singleton() { return &_singleton; }

private:
    static AP_SarusLock _singleton;

    // the signed message; the layout is shared with Sarus Operation Planner
    struct PACKED SignedBlock {
        char magic[12];        // "SARUS-LOCK1" and a NUL
        uint32_t operation;    // Op::UNLOCK
        uint8_t target_system; // this aircraft
        uint8_t target_component;
        uint8_t gcs_sysid;     // the station that will hold the unlock
        uint8_t nonce[NONCE_LEN];
    };

    uint8_t status_flags(mavlink_channel_t chan, uint8_t sysid);
    bool unlocked_for(mavlink_channel_t chan, uint8_t sysid);
    void expire_unlock();
    void make_nonce();
    void refuse_unlock(mavlink_channel_t chan, const mavlink_secure_command_t &pkt, uint8_t gcs_sysid,
                       const char *why);
    bool signature_ok(const SignedBlock &block, const uint8_t sig[SIG_LEN]) const;
    void send_reply(mavlink_channel_t chan, const mavlink_secure_command_t &pkt, MAV_RESULT result,
                    bool with_nonce, uint8_t gcs_sysid);

    HAL_Semaphore sem;

    bool unlocked;
    mavlink_channel_t unlocked_chan;
    uint8_t unlocked_sysid;
    uint32_t unlocked_last_seen_ms;

    uint8_t nonce[NONCE_LEN];
    bool nonce_valid;
    uint32_t nonce_ms;
    mavlink_channel_t nonce_chan;
    uint8_t nonce_sysid;

    uint32_t last_denied_text_ms;
    uint32_t last_refused_text_ms;
    uint32_t last_failed_check_ms;

    // packet arrival times, folded in as they come; part of every nonce
    uint8_t entropy[32];
    uint8_t entropy_pos;
};

namespace AP {
    AP_SarusLock &sarus_lock();
};

#endif // AP_SARUS_LOCK_ENABLED
