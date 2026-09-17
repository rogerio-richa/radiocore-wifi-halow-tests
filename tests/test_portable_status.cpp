#include <cassert>
#include <cstdint>
#include <cstring>

#include "../firmware/portable-gateway/portable_status.h"

using portable_status::IPv4;
using portable_status::RateTracker;
using portable_status::Snapshot;
using portable_status::Tone;
using portable_status::TrafficRates;
using portable_status::TrafficTotals;
using portable_status::View;

static void test_rates_map_rx_toward_phone_and_tx_toward_base()
{
    RateTracker tracker = {};
    TrafficRates rates = {};

    assert(!portable_status::sample_rates(
        &tracker, 1000, true, {1000, 500}, &rates));
    assert(!rates.available);

    assert(portable_status::sample_rates(
        &tracker, 2000, true, {51000, 1500}, &rates));
    assert(rates.available);
    assert(rates.toward_phone_kbps == 400);
    assert(rates.toward_base_kbps == 8);
}

static void test_rates_suppress_resets_and_unavailable_counters()
{
    RateTracker tracker = {};
    TrafficRates rates = {};

    assert(!portable_status::sample_rates(
        &tracker, 1000, true, {1000, 500}, &rates));
    assert(portable_status::sample_rates(
        &tracker, 2000, true, {2000, 1500}, &rates));

    assert(!portable_status::sample_rates(
        &tracker, 3000, true, {1, 1}, &rates));
    assert(!rates.available);

    assert(!portable_status::sample_rates(
        &tracker, 3000, true, {2, 2}, &rates));
    assert(!rates.available);

    assert(!portable_status::sample_rates(
        &tracker, 4000, false, {3, 3}, &rates));
    assert(!rates.available);

    assert(!portable_status::sample_rates(
        &tracker, 5000, true, {100, 100}, &rates));
    assert(portable_status::sample_rates(
        &tracker, 6000, true, {100, 100}, &rates));
    assert(rates.available);
    assert(rates.toward_phone_kbps == 0);
    assert(rates.toward_base_kbps == 0);
}

static void test_rssi_tones_use_explicit_thresholds()
{
    assert(portable_status::rssi_tone(-69) == Tone::Good);
    assert(portable_status::rssi_tone(-70) == Tone::Good);
    assert(portable_status::rssi_tone(-71) == Tone::Warning);
    assert(portable_status::rssi_tone(-90) == Tone::Warning);
    assert(portable_status::rssi_tone(-91) == Tone::Critical);
}

static Snapshot connected_snapshot(uint32_t now_ms, TrafficTotals totals)
{
    Snapshot snapshot = {};
    snapshot.now_ms = now_ms;
    snapshot.connected = true;
    snapshot.rssi_valid = true;
    snapshot.rssi_dbm = -67;
    snapshot.counters_available = true;
    snapshot.traffic = totals;
    snapshot.wifi_clients = 1;
    snapshot.halow_ip = IPv4{{10, 42, 0, 2}};
    return snapshot;
}

static void test_connected_view_formats_large_type_measurements()
{
    const View first = portable_status::make_view(
        connected_snapshot(1000, {1000, 500}));
    assert(std::strcmp(first.link, "LINK UP") == 0);
    assert(std::strcmp(first.rssi, "-67dBm") == 0);
    assert(std::strcmp(first.clients, "WIFI 1") == 0);
    assert(std::strlen(first.link) <= 7);
    assert(std::strlen(first.rssi) <= 7);
    assert(std::strlen(first.clients) <= 7);
    assert(std::strcmp(first.channel, "CH 41") == 0);
    assert(std::strcmp(first.frequency, "922.5") == 0);
    assert(std::strcmp(first.bandwidth, "1 MHz") == 0);
    assert(std::strlen(first.channel) <= 7);
    assert(std::strlen(first.frequency) <= 7);
    assert(std::strlen(first.bandwidth) <= 7);
    assert(first.link_tone == Tone::Good);
    assert(first.rssi_tone == Tone::Good);
}

static void test_searching_view_never_presents_stale_radio_values()
{
    Snapshot snapshot = connected_snapshot(90061000, {51000, 1500});
    snapshot.connected = false;
    snapshot.rssi_valid = true;
    snapshot.rssi_dbm = -40;
    snapshot.wifi_clients = 0;

    const View view = portable_status::make_view(snapshot);
    assert(std::strcmp(view.link, "SEARCH") == 0);
    assert(std::strcmp(view.rssi, "--dBm") == 0);
    assert(std::strcmp(view.channel, "CH 41") == 0);
    assert(std::strcmp(view.frequency, "922.5") == 0);
    assert(std::strcmp(view.bandwidth, "1 MHz") == 0);
    assert(view.link_tone == Tone::Warning);
    assert(view.rssi_tone == Tone::Muted);
}

int main()
{
    test_rates_map_rx_toward_phone_and_tx_toward_base();
    test_rates_suppress_resets_and_unavailable_counters();
    test_rssi_tones_use_explicit_thresholds();
    test_connected_view_formats_large_type_measurements();
    test_searching_view_never_presents_stale_radio_values();
    return 0;
}
