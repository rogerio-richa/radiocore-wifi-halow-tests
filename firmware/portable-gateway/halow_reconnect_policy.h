#ifndef RC32_HALOW_RECONNECT_POLICY_H
#define RC32_HALOW_RECONNECT_POLICY_H

#include <stdint.h>

namespace halow_reconnect {

static const uint32_t kDisconnectGraceMs = 5000;
static const uint32_t kRetryIntervalMs = 15000;

struct State {
  uint32_t disconnected_since_ms;
  uint32_t last_attempt_ms;
  bool disconnected;
  bool attempted;
};

inline bool became_connected(State *state, bool connected)
{
  if (!connected || !state->disconnected) {
    return false;
  }

  *state = {};
  return true;
}

inline bool should_attempt(State *state, bool connected, uint32_t now_ms)
{
  if (connected) {
    *state = {};
    return false;
  }

  if (!state->disconnected) {
    state->disconnected = true;
    state->disconnected_since_ms = now_ms;
    state->attempted = false;
    return false;
  }

  if (now_ms - state->disconnected_since_ms < kDisconnectGraceMs) {
    return false;
  }
  if (state->attempted &&
      now_ms - state->last_attempt_ms < kRetryIntervalMs) {
    return false;
  }

  state->last_attempt_ms = now_ms;
  state->attempted = true;
  return true;
}

}  // namespace halow_reconnect

#endif
