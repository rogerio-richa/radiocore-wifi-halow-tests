/*
 * Passphrases shared by the base and portable gateway sketches.
 *
 * Copy this file to rc32_secrets.h (ignored by Git) and define your own
 * values there. Anything left undefined falls back to the placeholders below.
 * Each passphrase must be 8 to 63 characters.
 */
#ifndef RC32_SECRETS_EXAMPLE_H
#define RC32_SECRETS_EXAMPLE_H

#ifndef RC32_HALOW_PASSPHRASE
#define RC32_HALOW_PASSPHRASE "change-this-passphrase"
#endif

#ifndef RC32_BASE_WIFI_PASSPHRASE
#define RC32_BASE_WIFI_PASSPHRASE "change-this-passphrase"
#endif

#ifndef RC32_PORTABLE_WIFI_PASSPHRASE
#define RC32_PORTABLE_WIFI_PASSPHRASE "change-this-passphrase"
#endif

#endif
