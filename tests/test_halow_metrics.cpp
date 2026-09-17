#include <cassert>
#include <cstdint>
#include <iostream>
#include <limits>

#include "../firmware/base-gateway/halow_metrics.h"


static uint32_t encoded_rate(uint32_t bandwidth, uint32_t mcs, uint32_t sgi)
{
    return bandwidth | (mcs << 4) | (sgi << 8);
}


int main()
{
    {
        assert(!halow_metrics::radio_activity_changed(100, 0, false));
        assert(!halow_metrics::radio_activity_changed(100, 100, true));
        assert(halow_metrics::radio_activity_changed(101, 100, true));
    }

    {
        const halow_metrics::DecodedRate one = halow_metrics::decode_rate_info(
            encoded_rate(0, 0, 0));
        const halow_metrics::DecodedRate two = halow_metrics::decode_rate_info(
            encoded_rate(1, 5, 0));
        const halow_metrics::DecodedRate four = halow_metrics::decode_rate_info(
            encoded_rate(2, 11, 1));
        assert(one.bandwidth_mhz == 1);
        assert(two.bandwidth_mhz == 2);
        assert(four.bandwidth_mhz == 4);
        assert(four.mcs == 11);
        assert(four.sgi == 1);
    }

    {
        const uint32_t previous = std::numeric_limits<uint32_t>::max() - 2;
        assert(halow_metrics::counter_delta(2, previous) == 5);
    }

    {
        const uint32_t rates[] = {
            encoded_rate(0, 1, 0),
            encoded_rate(1, 7, 1),
            encoded_rate(2, 3, 0),
        };
        const uint32_t sent[] = {110, 80, 400};
        const uint32_t success[] = {107, 70, 390};
        const uint32_t previous_sent[] = {100, 20, 300};
        const uint32_t previous_success[] = {100, 20, 300};

        const halow_metrics::RateIntervalSummary summary =
            halow_metrics::summarize_rate_interval(
                rates, sent, success, previous_sent, previous_success, 3);

        assert(summary.traffic);
        assert(summary.attempts == 170);
        assert(summary.successes == 147);
        assert(summary.mcs == 3);
        assert(summary.bandwidth_mhz == 4);
        assert(summary.sgi == 0);
    }

    {
        const uint32_t rates[] = {
            encoded_rate(0, 2, 0),
            encoded_rate(0, 8, 1),
        };
        const uint32_t sent[] = {1001, 110};
        const uint32_t success[] = {1001, 110};
        const uint32_t previous_sent[] = {1000, 100};
        const uint32_t previous_success[] = {1000, 100};

        const halow_metrics::RateIntervalSummary summary =
            halow_metrics::summarize_rate_interval(
                rates, sent, success, previous_sent, previous_success, 2);

        assert(summary.mcs == 8);
        assert(summary.sgi == 1);
    }

    {
        const uint32_t rates[] = {encoded_rate(0, 4, 0)};
        const uint32_t sent[] = {50};
        const uint32_t success[] = {48};

        const halow_metrics::RateIntervalSummary summary =
            halow_metrics::summarize_rate_interval(
                rates, sent, success, sent, success, 1);

        assert(!summary.traffic);
        assert(summary.attempts == 0);
        assert(summary.successes == 0);
        assert(summary.mcs == -1);
        assert(summary.bandwidth_mhz == 0);
        assert(summary.sgi == -1);
    }

    {
        const uint32_t rates[] = {encoded_rate(0, 1, 0)};
        const uint32_t sent[] = {10};
        const uint32_t success[] = {20};
        const uint32_t previous[] = {5};

        const halow_metrics::RateIntervalSummary summary =
            halow_metrics::summarize_rate_interval(
                rates, sent, success, previous, previous, 1);

        assert(summary.attempts == 5);
        assert(summary.successes == 5);
    }

    std::cout << "PASS: HaLow rate interval summarizer\n";
    return 0;
}
