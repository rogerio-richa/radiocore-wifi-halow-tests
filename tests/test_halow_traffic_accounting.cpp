#include <cassert>
#include <cstdint>

#include "../firmware/base-gateway/halow_traffic_accounting.h"

int main()
{
    halow_traffic::Totals totals;

    totals.record_rx(120, 0);
    totals.record_rx(33, -1);
    totals.record_tx(80, 0);
    totals.record_tx(17, -1);

    const halow_traffic::Snapshot snapshot = totals.snapshot();
    assert(snapshot.rx_bytes_total == 120);
    assert(snapshot.tx_bytes_total == 80);

    return 0;
}
