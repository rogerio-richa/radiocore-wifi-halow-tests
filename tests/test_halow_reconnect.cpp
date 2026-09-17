#include <cassert>
#include <cstdint>

#include "../firmware/portable-gateway/halow_reconnect_policy.h"

using halow_reconnect::State;

static void test_waits_before_first_retry()
{
    State state = {};

    assert(!halow_reconnect::should_attempt(&state, false, 1000));
    assert(!halow_reconnect::should_attempt(&state, false, 5999));
    assert(halow_reconnect::should_attempt(&state, false, 6000));
}

static void test_spaces_retries_and_resets_after_connection()
{
    State state = {};

    assert(!halow_reconnect::should_attempt(&state, false, 1000));
    assert(halow_reconnect::should_attempt(&state, false, 6000));
    assert(!halow_reconnect::should_attempt(&state, false, 20999));
    assert(halow_reconnect::should_attempt(&state, false, 21000));
    assert(!halow_reconnect::should_attempt(&state, true, 22000));
    assert(!halow_reconnect::should_attempt(&state, false, 22001));
    assert(halow_reconnect::should_attempt(&state, false, 27001));
}

static void test_reports_one_recovery_after_a_disconnect()
{
    State state = {};

    assert(!halow_reconnect::became_connected(&state, true));
    assert(!halow_reconnect::became_connected(&state, false));
    assert(!halow_reconnect::should_attempt(&state, false, 1000));
    assert(halow_reconnect::became_connected(&state, true));
    assert(!halow_reconnect::became_connected(&state, true));
}

int main()
{
    test_waits_before_first_retry();
    test_spaces_retries_and_resets_after_connection();
    test_reports_one_recovery_after_a_disconnect();
    return 0;
}
