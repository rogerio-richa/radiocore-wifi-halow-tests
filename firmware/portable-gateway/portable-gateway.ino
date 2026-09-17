/*
 * Throwaway RC32 gateway based on Heltec's official
 * NAPT_HalowSTA_STATIC_to_WiFiAP example.
 */
#include <Arduino.h>
#include <HaLow.h>
#include <WiFi.h>
#include <dhcpserver/dhcpserver.h>
#include <esp_netif.h>
#include <esp_netif_net_stack.h>
#include <lwip/netif.h>
#include <lwip/pbuf.h>
#include "mmipal.h"
#include "mmwlan.h"
#include "../base-gateway/halow_traffic_accounting.h"
#include "halow_reconnect_policy.h"
#if __has_include("../rc32_secrets.h")
#include "../rc32_secrets.h"
#endif
#include "../rc32_secrets.example.h"
#include "portable_status.h"
#include "portable_tft.h"

#define HALOW_REGION     "US"
#define HALOW_AP_SSID    "RC32-HaLow-Backbone"
#define HALOW_PASSWORD   RC32_HALOW_PASSPHRASE

IPAddress halow_IP(10, 42, 0, 2);
IPAddress halow_gateway(10, 42, 0, 1);
IPAddress halow_subnet(255, 255, 255, 0);
IPAddress primaryDNS(8, 8, 8, 8);
IPAddress secondaryDNS(8, 8, 4, 4);

#define TAG              "PORTABLE"
#define AP_SSID          "RC32-HaLow"
#define AP_PASSWORD      RC32_PORTABLE_WIFI_PASSPHRASE
#define AP_CHANNEL       6
#define UPSTREAM_DNS     "8.8.8.8"

IPAddress phone_AP_IP(10, 43, 0, 1);
IPAddress phone_AP_gateway(10, 43, 0, 1);
IPAddress phone_AP_subnet(255, 255, 255, 0);

static struct netif *accounted_halow_netif = nullptr;
static netif_input_fn original_halow_input = nullptr;
static netif_linkoutput_fn original_halow_linkoutput = nullptr;
static halow_traffic::Totals halow_traffic_totals;
static portMUX_TYPE halow_traffic_lock = portMUX_INITIALIZER_UNLOCKED;
static bool halow_traffic_installed = false;
static bool phone_ap_started = false;
static uint32_t last_display_ms = 0;
static uint32_t last_serial_ms = 0;
static halow_reconnect::State halow_reconnect_state = {};

extern "C" err_t halow_mmnetif_input(struct pbuf *packet,
                                      struct netif *interface) {
  if (interface == nullptr || interface->input == nullptr) {
    return ERR_IF;
  }
  return interface->input(packet, interface);
}

static err_t counted_halow_input(struct pbuf *packet, struct netif *interface) {
  if (original_halow_input == nullptr) {
    return ERR_IF;
  }

  const uint32_t frame_bytes = packet == nullptr ? 0 : packet->tot_len;
  const err_t result = original_halow_input(packet, interface);
  portENTER_CRITICAL(&halow_traffic_lock);
  halow_traffic_totals.record_rx(frame_bytes, result);
  portEXIT_CRITICAL(&halow_traffic_lock);
  return result;
}

static err_t counted_halow_linkoutput(struct netif *interface,
                                      struct pbuf *packet) {
  if (original_halow_linkoutput == nullptr) {
    return ERR_IF;
  }

  const uint32_t frame_bytes = packet == nullptr ? 0 : packet->tot_len;
  const err_t result = original_halow_linkoutput(interface, packet);
  portENTER_CRITICAL(&halow_traffic_lock);
  halow_traffic_totals.record_tx(frame_bytes, result);
  portEXIT_CRITICAL(&halow_traffic_lock);
  return result;
}

static bool install_halow_traffic_accounting(struct netif *interface) {
  if (interface == nullptr || interface->input == nullptr ||
      interface->linkoutput == nullptr) {
    return false;
  }

  accounted_halow_netif = interface;
  original_halow_input = interface->input;
  original_halow_linkoutput = interface->linkoutput;
  interface->input = counted_halow_input;
  interface->linkoutput = counted_halow_linkoutput;
  halow_traffic_installed = true;
  return true;
}

static bool restore_halow_forwarding() {
  struct netif *interface = HaLow.netif();
  if (interface == nullptr) {
    return false;
  }

  const bool accounting_is_current = halow_traffic_installed &&
      accounted_halow_netif == interface &&
      interface->input == counted_halow_input &&
      interface->linkoutput == counted_halow_linkoutput;
  if (!accounting_is_current && !install_halow_traffic_accounting(interface)) {
    return false;
  }

  netif_set_default(interface);
  return true;
}

static bool snapshot_halow_traffic(halow_traffic::Snapshot *snapshot) {
  if (!halow_traffic_installed || accounted_halow_netif == nullptr ||
      accounted_halow_netif->input != counted_halow_input ||
      accounted_halow_netif->linkoutput != counted_halow_linkoutput) {
    *snapshot = {0, 0};
    return false;
  }

  portENTER_CRITICAL(&halow_traffic_lock);
  *snapshot = halow_traffic_totals.snapshot();
  portEXIT_CRITICAL(&halow_traffic_lock);
  return true;
}

static portable_status::IPv4 portable_ipv4(const IPAddress &address) {
  return {{address[0], address[1], address[2], address[3]}};
}

static portable_status::Snapshot portable_snapshot(uint32_t now) {
  halow_traffic::Snapshot traffic = {0, 0};
  const bool traffic_available = snapshot_halow_traffic(&traffic);
  const bool connected = HaLow.status() == WL_CONNECTED;

  portable_status::Snapshot snapshot = {};
  snapshot.now_ms = now;
  snapshot.connected = connected;
  snapshot.rssi_valid = connected;
  snapshot.rssi_dbm = connected ? HaLow.RSSI() : 0;
  snapshot.counters_available = traffic_available;
  snapshot.traffic = {traffic.rx_bytes_total, traffic.tx_bytes_total};
  snapshot.wifi_clients = phone_ap_started
      ? static_cast<uint8_t>(WiFi.softAPgetStationNum())
      : 0;
  snapshot.halow_ip = portable_ipv4(connected ? HaLow.localIP() : halow_IP);
  return snapshot;
}

static void update_display(uint32_t now) {
  const portable_status::View view = portable_status::make_view(
      portable_snapshot(now));
  portable_tft::render(view);
}

static void render_boot_state(uint32_t now) {
  portable_status::Snapshot snapshot = {};
  snapshot.now_ms = now;
  snapshot.halow_ip = portable_ipv4(halow_IP);
  const portable_status::View view = portable_status::make_view(snapshot);
  portable_tft::render(view);
}

static void maintain_halow_connection(uint32_t now) {
  const bool connected = HaLow.status() == WL_CONNECTED;
  if (halow_reconnect::became_connected(&halow_reconnect_state, connected)) {
    if (restore_halow_forwarding()) {
      Serial.printf("[%s] HaLow reconnected; forwarding restored\n", TAG);
    } else {
      Serial.printf("[%s] HaLow reconnected but forwarding restore failed\n", TAG);
    }
    return;
  }
  if (connected) {
    return;
  }
  if (!halow_reconnect::should_attempt(
          &halow_reconnect_state, false, now)) {
    return;
  }

  Serial.printf("[%s] HaLow reconnect attempt after %lu seconds disconnected\n",
                TAG,
                static_cast<unsigned long>(
                    (now - halow_reconnect_state.disconnected_since_ms) / 1000));
  const wl_status_t result = HaLow.begin(HALOW_AP_SSID, HALOW_PASSWORD);
  Serial.printf("[%s] HaLow reconnect request returned status=%d\n",
                TAG, static_cast<int>(result));
}

static bool softap_with_napt() {
  if (!WiFi.mode(WIFI_AP)) {
    Serial.printf("[%s] WiFi.mode(WIFI_AP) failed\n", TAG);
    return false;
  }

  delay(10);
  if (!WiFi.softAPConfig(phone_AP_IP, phone_AP_gateway, phone_AP_subnet)) {
    Serial.printf("[%s] softAPConfig failed\n", TAG);
    return false;
  }

  esp_netif_t* ap_netif = esp_netif_get_handle_from_ifkey("WIFI_AP_DEF");
  if (ap_netif == nullptr) {
    Serial.printf("[%s] AP netif unavailable\n", TAG);
    return false;
  }

  dhcps_offer_t offer_dns = OFFER_DNS;
  esp_netif_dhcps_option(ap_netif, ESP_NETIF_OP_SET,
                         ESP_NETIF_DOMAIN_NAME_SERVER,
                         &offer_dns, sizeof(offer_dns));

  esp_netif_dns_info_t dns_info = {};
  dns_info.ip.u_addr.ip4.addr = ipaddr_addr(UPSTREAM_DNS);
  dns_info.ip.type = ESP_IPADDR_TYPE_V4;
  esp_netif_set_dns_info(ap_netif, ESP_NETIF_DNS_MAIN, &dns_info);

  if (!WiFi.softAP(AP_SSID, AP_PASSWORD, AP_CHANNEL)) {
    Serial.printf("[%s] softAP failed\n", TAG);
    return false;
  }
  phone_ap_started = true;
  Serial.printf("[%s] softAP up: SSID=%s IP=%s\n",
                TAG, AP_SSID, WiFi.softAPIP().toString().c_str());

  esp_err_t err = esp_netif_napt_enable(ap_netif);
  if (err != ESP_OK) {
    Serial.printf("[%s] esp_netif_napt_enable failed: %s (0x%x)\n",
                  TAG, esp_err_to_name(err), err);
    return false;
  }
  Serial.printf("[%s] NAPT enabled on AP netif\n", TAG);

  if (!restore_halow_forwarding()) {
    Serial.printf("[%s] default route restore failed\n", TAG);
    return false;
  }
  Serial.printf("[%s] default route set to HaLow netif\n", TAG);
  return true;
}

void setup() {
  Serial.begin(115200);

#ifdef HT_RC3268
  pinMode(HALOW_LDO_CTRL, OUTPUT);
  digitalWrite(HALOW_LDO_CTRL, HALOW_LDO_ENABLE);
#endif

  const bool display_ready = portable_tft::begin();
  Serial.printf(
      "[%s] TFT panel id=0x%06lX expected=0x%06lX state=%s\n",
      TAG,
      static_cast<unsigned long>(portable_tft::panel_id()),
      static_cast<unsigned long>(portable_tft::kExpectedPanelId),
      display_ready ? "ready" : "headless");
  render_boot_state(millis());

  Serial.printf("[%s] connecting to HaLow SSID %s\n", TAG, HALOW_AP_SSID);

  if (!HaLow.config(halow_IP, halow_gateway, halow_subnet,
                    primaryDNS, secondaryDNS)) {
    Serial.printf("[%s] HaLow static configuration failed\n", TAG);
  }

  HaLow.init(HALOW_REGION);
  HaLow.begin(HALOW_AP_SSID, HALOW_PASSWORD);
  uint32_t last_search_display_ms = millis();
  while (HaLow.status() != WL_CONNECTED) {
    const uint32_t now = millis();
    if (now - last_search_display_ms >= 1000) {
      last_search_display_ms = now;
      update_display(now);
      Serial.print(".");
    }
    delay(50);
  }

  Serial.println();
  Serial.printf("[%s] HaLow connected: IP=%s MAC=%s gateway=%s\n",
                TAG,
                HaLow.localIP().toString().c_str(),
                HaLow.macAddress().c_str(),
                HaLow.gatewayIP().toString().c_str());

  if (restore_halow_forwarding()) {
    Serial.printf("[%s] HaLow interface traffic accounting enabled\n", TAG);
  } else {
    Serial.printf("[%s] HaLow interface traffic accounting unavailable\n", TAG);
  }

  if (!softap_with_napt()) {
    Serial.printf("[%s] phone gateway setup failed\n", TAG);
  }

  last_display_ms = millis();
  last_serial_ms = last_display_ms;
  update_display(last_display_ms);
}

void loop() {
  const uint32_t now = millis();
  maintain_halow_connection(now);
  if (now - last_display_ms >= 1000) {
    last_display_ms = now;
    update_display(now);
  }

  if (now - last_serial_ms >= 5000) {
    last_serial_ms = now;
    if (HaLow.status() == WL_CONNECTED) {
      Serial.printf("[%s time=%u s] HaLow RSSI=%d dBm\n",
                    TAG, now / 1000, HaLow.RSSI());
    } else {
      Serial.printf("[%s time=%u s] HaLow searching\n",
                    TAG, now / 1000);
    }
  }
  delay(5);
}
