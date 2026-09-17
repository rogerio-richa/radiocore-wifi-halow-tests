#pragma once

#include <stddef.h>
#include <stdint.h>

#include "portable_status.h"

namespace portable_tft
{

constexpr uint16_t kWidth = 128;
constexpr uint16_t kHeight = 220;
constexpr uint32_t kExpectedPanelId = 0x300101;
constexpr size_t kLayoutRowCount = 6;

struct LayoutRow
{
    uint16_t top;
    uint16_t bottom;
    const char *text;
    uint8_t scale;
    portable_status::Tone tone;
};

bool begin();
bool present();
uint32_t panel_id();
void backlight(bool enabled);
void plan_rows(const portable_status::View &view,
               LayoutRow (&rows)[kLayoutRowCount]);
void render(const portable_status::View &view);

} // namespace portable_tft
