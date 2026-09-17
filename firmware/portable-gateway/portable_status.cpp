#include "portable_status.h"

#include <limits.h>
#include <stdio.h>
#include <string.h>

namespace portable_status
{
namespace
{

void reset_rates(TrafficRates *rates)
{
    rates->available = false;
    rates->toward_phone_kbps = 0;
    rates->toward_base_kbps = 0;
}

void prime(RateTracker *tracker, uint32_t now_ms, TrafficTotals totals)
{
    tracker->primed = true;
    tracker->previous_ms = now_ms;
    tracker->previous = totals;
}

uint32_t kbps(uint64_t bytes, uint32_t elapsed_ms)
{
    const uint64_t rate = bytes * 8ULL / elapsed_ms;
    return rate > UINT32_MAX ? UINT32_MAX : static_cast<uint32_t>(rate);
}

} // namespace

bool sample_rates(RateTracker *tracker,
                  uint32_t now_ms,
                  bool counters_available,
                  TrafficTotals totals,
                  TrafficRates *rates)
{
    reset_rates(rates);
    if (!counters_available)
    {
        tracker->primed = false;
        return false;
    }

    if (!tracker->primed || now_ms <= tracker->previous_ms ||
        totals.rx_bytes_total < tracker->previous.rx_bytes_total ||
        totals.tx_bytes_total < tracker->previous.tx_bytes_total)
    {
        prime(tracker, now_ms, totals);
        return false;
    }

    const uint32_t elapsed_ms = now_ms - tracker->previous_ms;
    const uint64_t rx_bytes =
        totals.rx_bytes_total - tracker->previous.rx_bytes_total;
    const uint64_t tx_bytes =
        totals.tx_bytes_total - tracker->previous.tx_bytes_total;

    rates->toward_phone_kbps = kbps(rx_bytes, elapsed_ms);
    rates->toward_base_kbps = kbps(tx_bytes, elapsed_ms);
    rates->available = true;
    prime(tracker, now_ms, totals);
    return true;
}

Tone rssi_tone(int16_t dbm)
{
    if (dbm >= -70)
    {
        return Tone::Good;
    }
    if (dbm >= -90)
    {
        return Tone::Warning;
    }
    return Tone::Critical;
}

View make_view(const Snapshot &snapshot)
{
    View view = {};
    snprintf(view.clients, sizeof(view.clients), "WIFI %u",
             static_cast<unsigned int>(snapshot.wifi_clients));
    snprintf(view.channel, sizeof(view.channel), "CH 41");
    snprintf(view.frequency, sizeof(view.frequency), "922.5");
    snprintf(view.bandwidth, sizeof(view.bandwidth), "1 MHz");

    if (!snapshot.connected)
    {
        snprintf(view.link, sizeof(view.link), "SEARCH");
        snprintf(view.rssi, sizeof(view.rssi), "--dBm");
        view.link_tone = Tone::Warning;
        view.rssi_tone = Tone::Muted;
        return view;
    }

    snprintf(view.link, sizeof(view.link), "LINK UP");
    view.link_tone = Tone::Good;

    if (snapshot.rssi_valid)
    {
        snprintf(view.rssi, sizeof(view.rssi), "%ddBm",
                 static_cast<int>(snapshot.rssi_dbm));
        view.rssi_tone = rssi_tone(snapshot.rssi_dbm);
    }
    else
    {
        snprintf(view.rssi, sizeof(view.rssi), "--dBm");
        view.rssi_tone = Tone::Muted;
    }

    return view;
}

} // namespace portable_status
