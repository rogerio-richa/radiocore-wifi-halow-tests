/*
 * Minimal NV3001B driver for the RadioCore portable gateway.
 *
 * The controller command sequence and classic 5x7 glyph data are adapted
 * from moononournation/Arduino_GFX commit
 * b4c3cbe2144c9c5942a073fb94a0a4ab9c23c5d6, specifically
 * src/display/Arduino_NV3001B.h and src/font/glcdfont.h. The complete BSD
 * notice is retained in ARDUINO_GFX_BSD_LICENSE.txt beside this source.
 */

#include "portable_tft.h"

#include <string.h>

namespace portable_tft
{

void plan_rows(const portable_status::View &view,
               LayoutRow (&rows)[kLayoutRowCount])
{
    rows[0] = LayoutRow{0, 36, view.link, 3, view.link_tone};
    rows[1] = LayoutRow{36, 72, view.rssi, 3, view.rssi_tone};
    rows[2] = LayoutRow{72, 108, view.clients, 3,
                        portable_status::Tone::Accent};
    rows[3] = LayoutRow{108, 144, view.channel, 3,
                        portable_status::Tone::Normal};
    rows[4] = LayoutRow{144, 180, view.frequency, 3,
                        portable_status::Tone::Normal};
    rows[5] = LayoutRow{180, 220, view.bandwidth, 3,
                        portable_status::Tone::Normal};
}

} // namespace portable_tft

#ifdef PORTABLE_TFT_HOST_TEST

namespace portable_tft
{

bool begin()
{
    return false;
}

bool present()
{
    return false;
}

uint32_t panel_id()
{
    return 0;
}

void backlight(bool)
{
}

void render(const portable_status::View &)
{
}

} // namespace portable_tft

#else

#include <Arduino.h>
#include <SPI.h>

namespace portable_tft
{
namespace
{

constexpr uint8_t kClockPin = 17;
constexpr uint8_t kDataPin = 38;
constexpr uint8_t kChipSelectPin = 39;
constexpr uint8_t kDataCommandPin = 16;
constexpr uint8_t kResetPin = 4;
constexpr uint8_t kEnablePin = 6;
constexpr uint8_t kBacklightPin = 5;
constexpr uint32_t kSpiHz = 4000000;

constexpr bool unique_pins(uint8_t a, uint8_t b, uint8_t c, uint8_t d,
                           uint8_t e, uint8_t f, uint8_t g)
{
    return a != b && a != c && a != d && a != e && a != f && a != g &&
           b != c && b != d && b != e && b != f && b != g &&
           c != d && c != e && c != f && c != g &&
           d != e && d != f && d != g &&
           e != f && e != g && f != g;
}

static_assert(kWidth == 128, "NV3001B width must remain 128");
static_assert(kHeight == 220, "NV3001B height must remain 220");
static_assert(unique_pins(kClockPin, kDataPin, kChipSelectPin,
                          kDataCommandPin, kResetPin, kEnablePin,
                          kBacklightPin),
              "TFT pins must be unique");
#ifdef HALOW_LDO_CTRL
static_assert(kClockPin != HALOW_LDO_CTRL && kDataPin != HALOW_LDO_CTRL &&
                  kChipSelectPin != HALOW_LDO_CTRL &&
                  kDataCommandPin != HALOW_LDO_CTRL &&
                  kResetPin != HALOW_LDO_CTRL &&
                  kEnablePin != HALOW_LDO_CTRL &&
                  kBacklightPin != HALOW_LDO_CTRL,
              "TFT pins must not drive the HaLow LDO");
#endif

constexpr uint8_t kReadDisplayId = 0x04;
constexpr uint8_t kReadId1 = 0xDA;
constexpr uint8_t kReadId2 = 0xDB;
constexpr uint8_t kReadId3 = 0xDC;
constexpr uint8_t kColumnAddress = 0x2A;
constexpr uint8_t kRowAddress = 0x2B;
constexpr uint8_t kMemoryWrite = 0x2C;

constexpr uint16_t kBlack = 0x0000;
constexpr uint16_t kWhite = 0xFFFF;
constexpr uint16_t kRed = 0xF800;
constexpr uint16_t kGreen = 0x07E0;
constexpr uint16_t kYellow = 0xFFE0;
constexpr uint16_t kCyan = 0x07FF;
constexpr uint16_t kGray = 0x8410;

struct InitOperation
{
    uint8_t command;
    uint8_t length;
    uint8_t data[2];
    uint16_t delay_ms;
};

#define INIT_CMD(command) {command, 0, {0, 0}, 0}
#define INIT_1(command, value) {command, 1, {value, 0}, 0}
#define INIT_2(command, first, second) {command, 2, {first, second}, 0}
#define INIT_DELAY(command, delay_value) {command, 0, {0, 0}, delay_value}

const InitOperation kInitialization[] = {
    INIT_1(0xFF, 0xA5), INIT_1(0x41, 0x00), INIT_1(0x50, 0x02),
    INIT_1(0x52, 0x6E), INIT_1(0x57, 0x02), INIT_1(0x46, 0x11),
    INIT_2(0x47, 0x00, 0x01), INIT_2(0x8F, 0x22, 0x03),
    INIT_1(0x9A, 0x78), INIT_1(0x9B, 0x78), INIT_1(0x9C, 0xA0),
    INIT_1(0x9D, 0x17), INIT_1(0x9E, 0xC1), INIT_1(0x83, 0x5A),
    INIT_1(0x84, 0xB6), INIT_1(0xFF, 0xA5), INIT_1(0x85, 0x5F),
    INIT_1(0x6E, 0x0F), INIT_1(0x7E, 0x0F), INIT_1(0x60, 0x00),
    INIT_1(0x70, 0x00), INIT_1(0x6D, 0x33), INIT_1(0x7D, 0x37),
    INIT_1(0x61, 0x09), INIT_1(0x71, 0x0A), INIT_1(0x6C, 0x2A),
    INIT_1(0x7C, 0x36), INIT_1(0x62, 0x11), INIT_1(0x72, 0x10),
    INIT_1(0x68, 0x4E), INIT_1(0x78, 0x4E), INIT_1(0x66, 0x36),
    INIT_1(0x76, 0x3C), INIT_1(0x1A, 0x1C), INIT_1(0x7B, 0x14),
    INIT_1(0x63, 0x0D), INIT_1(0x73, 0x0A), INIT_1(0x6A, 0x16),
    INIT_1(0x7A, 0x12), INIT_1(0x64, 0x0B), INIT_1(0x74, 0x0A),
    INIT_1(0x69, 0x08), INIT_1(0x79, 0x0A), INIT_1(0x65, 0x06),
    INIT_1(0x75, 0x07), INIT_1(0x67, 0x23), INIT_1(0x77, 0x44),
    INIT_1(0xE0, 0x00), INIT_1(0xE9, 0x30), INIT_1(0xEB, 0xB7),
    INIT_1(0xEC, 0x00), INIT_1(0xED, 0x11), INIT_1(0xF0, 0xB7),
    INIT_1(0x53, 0x04), INIT_1(0x54, 0x04), INIT_1(0x55, 0x40),
    INIT_1(0x56, 0x40), INIT_2(0xA0, 0x60, 0x01),
    INIT_1(0xA1, 0x84), INIT_1(0xA2, 0x85),
    INIT_2(0xAB, 0x00, 0x02), INIT_2(0xAC, 0x00, 0x06),
    INIT_2(0xAD, 0x00, 0x03), INIT_2(0xAE, 0x00, 0x07),
    INIT_1(0xC7, 0x01), INIT_1(0xB9, 0x82), INIT_1(0xBA, 0x83),
    INIT_1(0xBB, 0x00), INIT_1(0xBC, 0x81), INIT_1(0xBD, 0x02),
    INIT_1(0xBE, 0x01), INIT_1(0xBF, 0x04), INIT_1(0xC0, 0x03),
    INIT_1(0xC8, 0x55), INIT_1(0xC9, 0xC9), INIT_1(0xCA, 0xC8),
    INIT_1(0xCB, 0xCB), INIT_1(0xCC, 0xCA), INIT_1(0xCD, 0x55),
    INIT_1(0xCE, 0xCE), INIT_1(0xCF, 0xCD), INIT_1(0xD0, 0xD0),
    INIT_1(0xD1, 0xCF), INIT_1(0xF2, 0x46), INIT_1(0xA8, 0x04),
    INIT_1(0xA9, 0xB0), INIT_1(0xAA, 0xA3), INIT_1(0xB6, 0x00),
    INIT_1(0xB7, 0xB0), INIT_1(0xB8, 0xA3), INIT_1(0xC4, 0x03),
    INIT_1(0xC5, 0xB0), INIT_1(0xC6, 0xA3), INIT_1(0x80, 0x10),
    INIT_1(0xFF, 0x00), INIT_1(0x35, 0x00),
    INIT_DELAY(0x11, 120),
    INIT_1(0x3A, 0x05), INIT_1(0x36, 0x00),
    INIT_DELAY(0x29, 10),
    INIT_CMD(0x20),
};

#undef INIT_CMD
#undef INIT_1
#undef INIT_2
#undef INIT_DELAY

const char kFontCharacters[] =
    " -.0123456789:?ABCDEFGHIJKLMNOPQRSTUVWXYZbdkmpsz";

const uint8_t kFont[][5] = {
    {0x00, 0x00, 0x00, 0x00, 0x00}, // space
    {0x08, 0x08, 0x08, 0x08, 0x08}, // -
    {0x00, 0x00, 0x60, 0x60, 0x00}, // .
    {0x3E, 0x51, 0x49, 0x45, 0x3E}, // 0
    {0x00, 0x42, 0x7F, 0x40, 0x00}, // 1
    {0x72, 0x49, 0x49, 0x49, 0x46}, // 2
    {0x21, 0x41, 0x49, 0x4D, 0x33}, // 3
    {0x18, 0x14, 0x12, 0x7F, 0x10}, // 4
    {0x27, 0x45, 0x45, 0x45, 0x39}, // 5
    {0x3C, 0x4A, 0x49, 0x49, 0x31}, // 6
    {0x41, 0x21, 0x11, 0x09, 0x07}, // 7
    {0x36, 0x49, 0x49, 0x49, 0x36}, // 8
    {0x46, 0x49, 0x49, 0x29, 0x1E}, // 9
    {0x00, 0x00, 0x14, 0x00, 0x00}, // :
    {0x02, 0x01, 0x59, 0x09, 0x06}, // ?
    {0x7C, 0x12, 0x11, 0x12, 0x7C}, // A
    {0x7F, 0x49, 0x49, 0x49, 0x36}, // B
    {0x3E, 0x41, 0x41, 0x41, 0x22}, // C
    {0x7F, 0x41, 0x41, 0x41, 0x3E}, // D
    {0x7F, 0x49, 0x49, 0x49, 0x41}, // E
    {0x7F, 0x09, 0x09, 0x09, 0x01}, // F
    {0x3E, 0x41, 0x41, 0x51, 0x73}, // G
    {0x7F, 0x08, 0x08, 0x08, 0x7F}, // H
    {0x00, 0x41, 0x7F, 0x41, 0x00}, // I
    {0x20, 0x40, 0x41, 0x3F, 0x01}, // J
    {0x7F, 0x08, 0x14, 0x22, 0x41}, // K
    {0x7F, 0x40, 0x40, 0x40, 0x40}, // L
    {0x7F, 0x02, 0x1C, 0x02, 0x7F}, // M
    {0x7F, 0x04, 0x08, 0x10, 0x7F}, // N
    {0x3E, 0x41, 0x41, 0x41, 0x3E}, // O
    {0x7F, 0x09, 0x09, 0x09, 0x06}, // P
    {0x3E, 0x41, 0x51, 0x21, 0x5E}, // Q
    {0x7F, 0x09, 0x19, 0x29, 0x46}, // R
    {0x26, 0x49, 0x49, 0x49, 0x32}, // S
    {0x03, 0x01, 0x7F, 0x01, 0x03}, // T
    {0x3F, 0x40, 0x40, 0x40, 0x3F}, // U
    {0x1F, 0x20, 0x40, 0x20, 0x1F}, // V
    {0x3F, 0x40, 0x38, 0x40, 0x3F}, // W
    {0x63, 0x14, 0x08, 0x14, 0x63}, // X
    {0x03, 0x04, 0x78, 0x04, 0x03}, // Y
    {0x61, 0x59, 0x49, 0x4D, 0x43}, // Z
    {0x7F, 0x28, 0x44, 0x44, 0x38}, // b
    {0x38, 0x44, 0x44, 0x28, 0x7F}, // d
    {0x7F, 0x10, 0x28, 0x44, 0x00}, // k
    {0x7C, 0x04, 0x78, 0x04, 0x78}, // m
    {0xFC, 0x18, 0x24, 0x24, 0x18}, // p
    {0x48, 0x54, 0x54, 0x54, 0x24}, // s
    {0x44, 0x64, 0x54, 0x4C, 0x44}, // z
};

static_assert(sizeof(kFont) / sizeof(kFont[0]) ==
                  sizeof(kFontCharacters) - 1,
              "font lookup and glyph table differ");

struct CachedRow
{
    char text[22];
    portable_status::Tone tone;
    bool valid;
};

SPIClass s_spi(HSPI);
bool s_present = false;
uint32_t s_panel_id = 0;
CachedRow s_cache[kLayoutRowCount] = {};

void probe_delay()
{
    delayMicroseconds(2);
}

void probe_clock_bit(bool bit)
{
    digitalWrite(kClockPin, LOW);
    digitalWrite(kDataPin, bit ? HIGH : LOW);
    probe_delay();
    digitalWrite(kClockPin, HIGH);
    probe_delay();
}

uint8_t probe_read_bit()
{
    digitalWrite(kClockPin, HIGH);
    probe_delay();
    const uint8_t bit = digitalRead(kDataPin) == HIGH ? 1U : 0U;
    digitalWrite(kClockPin, LOW);
    probe_delay();
    return bit;
}

void probe_read(uint8_t command, uint8_t *bytes, size_t count,
                uint8_t dummy_bits)
{
    digitalWrite(kChipSelectPin, LOW);
    digitalWrite(kDataCommandPin, LOW);
    pinMode(kDataPin, OUTPUT);
    for (uint8_t mask = 0x80; mask != 0; mask >>= 1)
    {
        probe_clock_bit((command & mask) != 0);
    }
    digitalWrite(kClockPin, LOW);
    digitalWrite(kDataCommandPin, HIGH);
    pinMode(kDataPin, INPUT);
    for (uint8_t bit = 0; bit < dummy_bits; ++bit)
    {
        (void)probe_read_bit();
    }
    for (size_t index = 0; index < count; ++index)
    {
        uint8_t value = 0;
        for (uint8_t bit = 0; bit < 8; ++bit)
        {
            value = static_cast<uint8_t>((value << 1) | probe_read_bit());
        }
        bytes[index] = value;
    }
    digitalWrite(kChipSelectPin, HIGH);
    pinMode(kDataPin, OUTPUT);
    digitalWrite(kDataPin, HIGH);
}

uint32_t assemble_id(const uint8_t bytes[3])
{
    return (static_cast<uint32_t>(bytes[0]) << 16) |
           (static_cast<uint32_t>(bytes[1]) << 8) |
           static_cast<uint32_t>(bytes[2]);
}

uint32_t probe_panel_id()
{
    uint8_t display_id[3] = {};
    uint8_t register_id[3] = {};
    probe_read(kReadDisplayId, display_id, 3, 1);
    probe_read(kReadId1, &register_id[0], 1, 0);
    probe_read(kReadId2, &register_id[1], 1, 0);
    probe_read(kReadId3, &register_id[2], 1, 0);

    const uint32_t first = assemble_id(display_id);
    const uint32_t second = assemble_id(register_id);
    if (first == kExpectedPanelId)
    {
        return first;
    }
    if (second == kExpectedPanelId)
    {
        return second;
    }
    return first != 0 ? first : second;
}

void write_command_data(uint8_t command, const uint8_t *data, uint8_t length)
{
    digitalWrite(kDataCommandPin, LOW);
    digitalWrite(kChipSelectPin, LOW);
    s_spi.transfer(command);
    digitalWrite(kChipSelectPin, HIGH);
    if (length == 0)
    {
        return;
    }

    digitalWrite(kDataCommandPin, HIGH);
    digitalWrite(kChipSelectPin, LOW);
    for (uint8_t index = 0; index < length; ++index)
    {
        s_spi.transfer(data[index]);
    }
    digitalWrite(kChipSelectPin, HIGH);
}

void send(uint8_t command, const uint8_t *data, uint8_t length)
{
    s_spi.beginTransaction(SPISettings(kSpiHz, MSBFIRST, SPI_MODE0));
    write_command_data(command, data, length);
    s_spi.endTransaction();
}

void initialize_controller()
{
    for (size_t index = 0;
         index < sizeof(kInitialization) / sizeof(kInitialization[0]);
         ++index)
    {
        const InitOperation &operation = kInitialization[index];
        send(operation.command, operation.data, operation.length);
        if (operation.delay_ms != 0)
        {
            delay(operation.delay_ms);
        }
    }
}

void set_window(uint16_t x, uint16_t y, uint16_t width, uint16_t height)
{
    const uint16_t x_end = x + width - 1;
    const uint16_t y_end = y + height - 1;
    const uint8_t columns[] = {
        static_cast<uint8_t>(x >> 8), static_cast<uint8_t>(x),
        static_cast<uint8_t>(x_end >> 8), static_cast<uint8_t>(x_end)};
    const uint8_t rows[] = {
        static_cast<uint8_t>(y >> 8), static_cast<uint8_t>(y),
        static_cast<uint8_t>(y_end >> 8), static_cast<uint8_t>(y_end)};
    write_command_data(kColumnAddress, columns, sizeof(columns));
    write_command_data(kRowAddress, rows, sizeof(rows));
    write_command_data(kMemoryWrite, nullptr, 0);
}

void stream_color(uint16_t color, uint32_t count)
{
    const uint8_t high = static_cast<uint8_t>(color >> 8);
    const uint8_t low = static_cast<uint8_t>(color);
    digitalWrite(kDataCommandPin, HIGH);
    digitalWrite(kChipSelectPin, LOW);
    for (uint32_t pixel = 0; pixel < count; ++pixel)
    {
        s_spi.transfer(high);
        s_spi.transfer(low);
    }
    digitalWrite(kChipSelectPin, HIGH);
}

void clear_screen()
{
    s_spi.beginTransaction(SPISettings(kSpiHz, MSBFIRST, SPI_MODE0));
    set_window(0, 0, kWidth, kHeight);
    stream_color(kBlack, static_cast<uint32_t>(kWidth) * kHeight);
    s_spi.endTransaction();
}

const uint8_t *glyph(char character)
{
    const char *match = strchr(kFontCharacters, character);
    if (match == nullptr)
    {
        match = strchr(kFontCharacters, '?');
    }
    return kFont[match - kFontCharacters];
}

uint16_t tone_color(portable_status::Tone tone)
{
    switch (tone)
    {
    case portable_status::Tone::Accent:
        return kCyan;
    case portable_status::Tone::Good:
        return kGreen;
    case portable_status::Tone::Warning:
        return kYellow;
    case portable_status::Tone::Critical:
        return kRed;
    case portable_status::Tone::Muted:
        return kGray;
    case portable_status::Tone::Normal:
    default:
        return kWhite;
    }
}

void draw_row(uint16_t y, uint16_t bottom, const char *text,
              uint8_t scale, portable_status::Tone tone)
{
    const uint16_t height = bottom - y;
    const uint16_t glyph_height = 8U * scale;
    const uint16_t top_padding = (height - glyph_height) / 2U;
    const uint16_t left_padding = scale == 3 ? 1U : 2U;
    const size_t text_length = strlen(text);
    const uint16_t foreground = tone_color(tone);

    s_spi.beginTransaction(SPISettings(kSpiHz, MSBFIRST, SPI_MODE0));
    set_window(0, y, kWidth, height);
    digitalWrite(kDataCommandPin, HIGH);
    digitalWrite(kChipSelectPin, LOW);
    for (uint16_t row = 0; row < height; ++row)
    {
        for (uint16_t column = 0; column < kWidth; ++column)
        {
            uint16_t color = kBlack;
            if (row >= top_padding && row < top_padding + glyph_height &&
                column >= left_padding)
            {
                const uint16_t relative_x = column - left_padding;
                const size_t character_index = relative_x / (6U * scale);
                const uint16_t character_x = relative_x % (6U * scale);
                if (character_index < text_length && character_x < 5U * scale)
                {
                    const uint8_t font_x = character_x / scale;
                    const uint8_t font_y = (row - top_padding) / scale;
                    const uint8_t *columns = glyph(text[character_index]);
                    if ((columns[font_x] & (1U << font_y)) != 0)
                    {
                        color = foreground;
                    }
                }
            }
            s_spi.transfer(static_cast<uint8_t>(color >> 8));
            s_spi.transfer(static_cast<uint8_t>(color));
        }
    }
    digitalWrite(kChipSelectPin, HIGH);
    s_spi.endTransaction();
}

void render_cached_row(size_t cache_index, uint16_t y, uint16_t bottom,
                       const char *text, uint8_t scale,
                       portable_status::Tone tone)
{
    CachedRow &cached = s_cache[cache_index];
    if (cached.valid && cached.tone == tone &&
        strncmp(cached.text, text, sizeof(cached.text)) == 0)
    {
        return;
    }

    draw_row(y, bottom, text, scale, tone);
    strncpy(cached.text, text, sizeof(cached.text) - 1);
    cached.text[sizeof(cached.text) - 1] = '\0';
    cached.tone = tone;
    cached.valid = true;
}

} // namespace

bool begin()
{
    s_present = false;
    s_panel_id = 0;
    memset(s_cache, 0, sizeof(s_cache));

    pinMode(kEnablePin, OUTPUT);
    digitalWrite(kEnablePin, LOW);
    pinMode(kBacklightPin, OUTPUT);
    digitalWrite(kBacklightPin, LOW);
    pinMode(kChipSelectPin, OUTPUT);
    pinMode(kClockPin, OUTPUT);
    pinMode(kDataPin, OUTPUT);
    pinMode(kDataCommandPin, OUTPUT);
    pinMode(kResetPin, OUTPUT);
    digitalWrite(kChipSelectPin, HIGH);
    digitalWrite(kClockPin, LOW);
    digitalWrite(kDataPin, HIGH);
    digitalWrite(kDataCommandPin, HIGH);
    digitalWrite(kResetPin, HIGH);
    delay(100);
    digitalWrite(kResetPin, LOW);
    delay(120);
    digitalWrite(kResetPin, HIGH);
    delay(120);

    s_panel_id = probe_panel_id();
    if (s_panel_id != kExpectedPanelId)
    {
        digitalWrite(kBacklightPin, LOW);
        digitalWrite(kEnablePin, HIGH);
        return false;
    }

    digitalWrite(kResetPin, HIGH);
    delay(100);
    digitalWrite(kResetPin, LOW);
    delay(120);
    digitalWrite(kResetPin, HIGH);
    delay(120);

    s_spi.begin(kClockPin, -1, kDataPin, -1);
    initialize_controller();
    s_present = true;
    clear_screen();
    digitalWrite(kBacklightPin, HIGH);
    return true;
}

bool present()
{
    return s_present;
}

uint32_t panel_id()
{
    return s_panel_id;
}

void backlight(bool enabled)
{
    if (enabled && !s_present)
    {
        return;
    }
    pinMode(kBacklightPin, OUTPUT);
    digitalWrite(kBacklightPin, enabled ? HIGH : LOW);
}

void render(const portable_status::View &view)
{
    if (!s_present)
    {
        return;
    }

    LayoutRow rows[kLayoutRowCount] = {};
    plan_rows(view, rows);
    for (size_t index = 0; index < kLayoutRowCount; ++index)
    {
        render_cached_row(index, rows[index].top, rows[index].bottom,
                          rows[index].text, rows[index].scale,
                          rows[index].tone);
    }
}

} // namespace portable_tft

#endif
