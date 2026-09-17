#pragma once

#include <stdint.h>

namespace portable_status
{

enum class Tone : uint8_t
{
    Normal,
    Accent,
    Good,
    Warning,
    Critical,
    Muted,
};

struct TrafficTotals
{
    uint64_t rx_bytes_total;
    uint64_t tx_bytes_total;
};

struct TrafficRates
{
    bool available;
    uint32_t toward_phone_kbps;
    uint32_t toward_base_kbps;
};

struct RateTracker
{
    bool primed;
    uint32_t previous_ms;
    TrafficTotals previous;
};

struct IPv4
{
    uint8_t octet[4];
};

struct Snapshot
{
    uint32_t now_ms;
    bool connected;
    bool rssi_valid;
    int16_t rssi_dbm;
    bool counters_available;
    TrafficTotals traffic;
    uint8_t wifi_clients;
    IPv4 halow_ip;
};

struct View
{
    char link[8];
    char rssi[8];
    char clients[8];
    char channel[8];
    char frequency[8];
    char bandwidth[8];
    Tone link_tone;
    Tone rssi_tone;
};

bool sample_rates(RateTracker *tracker,
                  uint32_t now_ms,
                  bool counters_available,
                  TrafficTotals totals,
                  TrafficRates *rates);

Tone rssi_tone(int16_t dbm);

View make_view(const Snapshot &snapshot);

} // namespace portable_status
