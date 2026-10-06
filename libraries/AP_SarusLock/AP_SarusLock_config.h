#pragma once

#include <AP_HAL/AP_HAL_Boards.h>
#include <GCS_MAVLink/GCS_config.h>

// Sarus parameter lock: parameter changes, calibrations, resets and file writes from any
// ground station are refused until the station proves it holds the owner's key.
#ifndef AP_SARUS_LOCK_ENABLED
#if HAL_GCS_ENABLED && !defined(HAL_BUILD_AP_PERIPH) && !defined(HAL_BOOTLOADER_BUILD)
#define AP_SARUS_LOCK_ENABLED 1
#else
#define AP_SARUS_LOCK_ENABLED 0
#endif
#endif
