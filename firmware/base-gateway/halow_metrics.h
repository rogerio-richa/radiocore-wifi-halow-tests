#pragma once

#include <cstddef>
#include <cstdint>


namespace halow_metrics {

struct DecodedRate
{
    uint8_t bandwidth_mhz;
    uint8_t mcs;
    uint8_t sgi;
};

struct RateIntervalSummary
{
    bool traffic;
    uint32_t attempts;
    uint32_t successes;
    int8_t mcs;
    uint8_t bandwidth_mhz;
    int8_t sgi;
};

inline uint32_t counter_delta(uint32_t current, uint32_t previous)
{
    return current - previous;
}

inline bool radio_activity_changed(
    uint32_t current_last_tx,
    uint32_t previous_last_tx,
    bool have_previous)
{
    return have_previous && current_last_tx != previous_last_tx;
}

inline DecodedRate decode_rate_info(uint32_t rate_info)
{
    const uint8_t encoded_bandwidth = static_cast<uint8_t>(rate_info & 0x0fU);
    uint8_t bandwidth_mhz = 0;
    switch (encoded_bandwidth)
    {
        case 0:
            bandwidth_mhz = 1;
            break;
        case 1:
            bandwidth_mhz = 2;
            break;
        case 2:
            bandwidth_mhz = 4;
            break;
        default:
            break;
    }
    DecodedRate decoded = {
        bandwidth_mhz,
        static_cast<uint8_t>((rate_info >> 4) & 0x0fU),
        static_cast<uint8_t>((rate_info >> 8) & 0x01U),
    };
    return decoded;
}

inline RateIntervalSummary summarize_rate_interval(
    const uint32_t *rate_info,
    const uint32_t *current_sent,
    const uint32_t *current_success,
    const uint32_t *previous_sent,
    const uint32_t *previous_success,
    std::size_t entry_count)
{
    RateIntervalSummary summary = {false, 0, 0, -1, 0, -1};
    if (rate_info == NULL || current_sent == NULL || current_success == NULL ||
        previous_sent == NULL || previous_success == NULL)
    {
        return summary;
    }

    uint64_t attempts = 0;
    uint64_t successes = 0;
    uint32_t dominant_attempts = 0;
    std::size_t dominant_index = 0;
    for (std::size_t index = 0; index < entry_count; ++index)
    {
        const uint32_t sent_delta = counter_delta(
            current_sent[index], previous_sent[index]);
        const uint32_t success_delta = counter_delta(
            current_success[index], previous_success[index]);
        attempts += sent_delta;
        successes += success_delta;
        if (sent_delta > dominant_attempts)
        {
            dominant_attempts = sent_delta;
            dominant_index = index;
        }
    }

    if (attempts == 0)
    {
        return summary;
    }

    const uint64_t maximum = 0xffffffffULL;
    summary.traffic = true;
    summary.attempts = static_cast<uint32_t>(attempts > maximum ? maximum : attempts);
    const uint32_t bounded_successes = static_cast<uint32_t>(
        successes > maximum ? maximum : successes);
    summary.successes = bounded_successes > summary.attempts
        ? summary.attempts
        : bounded_successes;
    const DecodedRate dominant = decode_rate_info(rate_info[dominant_index]);
    summary.mcs = static_cast<int8_t>(dominant.mcs);
    summary.bandwidth_mhz = dominant.bandwidth_mhz;
    summary.sgi = static_cast<int8_t>(dominant.sgi);
    return summary;
}

}  // namespace halow_metrics
