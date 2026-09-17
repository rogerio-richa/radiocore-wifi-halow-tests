#pragma once

#include <stdint.h>

namespace halow_traffic
{

struct Snapshot
{
    uint64_t rx_bytes_total;
    uint64_t tx_bytes_total;
};

class Totals
{
  public:
    Totals() : rx_bytes_total_(0), tx_bytes_total_(0) {}

    void record_rx(uint32_t frame_bytes, int callback_result)
    {
        if (callback_result == 0)
        {
            rx_bytes_total_ += frame_bytes;
        }
    }

    void record_tx(uint32_t frame_bytes, int callback_result)
    {
        if (callback_result == 0)
        {
            tx_bytes_total_ += frame_bytes;
        }
    }

    Snapshot snapshot() const
    {
        return {rx_bytes_total_, tx_bytes_total_};
    }

  private:
    uint64_t rx_bytes_total_;
    uint64_t tx_bytes_total_;
};

} // namespace halow_traffic
