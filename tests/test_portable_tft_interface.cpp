#include <cassert>
#include <cstring>

#include "../firmware/portable-gateway/portable_tft.h"

int main()
{
    static_assert(portable_tft::kWidth == 128, "unexpected panel width");
    static_assert(portable_tft::kHeight == 220, "unexpected panel height");
    static_assert(portable_tft::kExpectedPanelId == 0x300101,
                  "unexpected NV3001B ID");

    assert(!portable_tft::present() ||
           portable_tft::panel_id() == portable_tft::kExpectedPanelId);

    portable_status::View view = {};
    std::strcpy(view.link, "LINK UP");
    std::strcpy(view.rssi, "-67dBm");
    std::strcpy(view.clients, "WIFI 1");
    std::strcpy(view.channel, "CH 41");
    std::strcpy(view.frequency, "922.5");
    std::strcpy(view.bandwidth, "1 MHz");
    view.link_tone = portable_status::Tone::Good;
    view.rssi_tone = portable_status::Tone::Warning;

    portable_tft::LayoutRow rows[portable_tft::kLayoutRowCount] = {};
    portable_tft::plan_rows(view, rows);

    const char *expected[] = {
        "LINK UP", "-67dBm", "WIFI 1", "CH 41", "922.5", "1 MHz"};
    static_assert(sizeof(expected) / sizeof(expected[0]) ==
                      portable_tft::kLayoutRowCount,
                  "expected row count differs from TFT layout");

    for (size_t index = 0; index < portable_tft::kLayoutRowCount; ++index)
    {
        assert(std::strcmp(rows[index].text, expected[index]) == 0);
        assert(rows[index].scale == 3);
        assert(rows[index].bottom > rows[index].top);
        assert(std::strlen(rows[index].text) * 6U * rows[index].scale + 1U <=
               portable_tft::kWidth);
        if (index != 0)
        {
            assert(rows[index].top == rows[index - 1].bottom);
        }
    }
    assert(rows[0].top == 0);
    assert(rows[portable_tft::kLayoutRowCount - 1].bottom ==
           portable_tft::kHeight);
    return 0;
}
