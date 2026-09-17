#include <Arduino.h>
#include <HaLow.h>
#include <WiFi.h>
#include <lwip/netif.h>
#include <lwip/pbuf.h>
#include "mmwlan.h"
#include "mmwlan_stats.h"
#include "halow_metrics.h"
#include "halow_traffic_accounting.h"
#if __has_include("../rc32_secrets.h")
#include "../rc32_secrets.h"
#endif
#include "../rc32_secrets.example.h"

#define HALOW_REGION     "US"
#define HALOW_CHANNEL    41
#define HALOW_AP_SSID    "RC32-HaLow-Backbone"
#define HALOW_PASSWORD   RC32_HALOW_PASSPHRASE

IPAddress halow_IP(10, 42, 0, 1);
IPAddress halow_gateway(10, 42, 0, 1);
IPAddress halow_subnet(255, 255, 255, 0);
IPAddress primaryDNS(8, 8, 8, 8);
IPAddress secondaryDNS(8, 8, 4, 4);

#define BASE_AP_SSID      "RC32-Base"
#define BASE_AP_PASSWORD  RC32_BASE_WIFI_PASSPHRASE
#define BASE_AP_CHANNEL   1

IPAddress mac_AP_IP(10, 41, 0, 1);
IPAddress mac_AP_gateway(10, 41, 0, 1);
IPAddress mac_AP_subnet(255, 255, 255, 0);

static uint32_t *previous_sent = nullptr;
static uint32_t *previous_success = nullptr;
static uint32_t previous_rate_entries = 0;
static struct mmwlan_stats_umac_data previous_umac = {};
static bool have_previous_umac = false;
static bool reported_rate_unavailable = false;
static uint32_t last_metric_ms = 0;
static uint32_t last_status_ms = 0;
static struct netif *accounted_halow_netif = nullptr;
static netif_input_fn original_halow_input = nullptr;
static netif_linkoutput_fn original_halow_linkoutput = nullptr;
static halow_traffic::Totals halow_traffic_totals;
static portMUX_TYPE halow_traffic_lock = portMUX_INITIALIZER_UNLOCKED;
static bool halow_traffic_installed = false;

struct UmacInterval
{
  bool radio_active;
  uint32_t txq_drops;
  uint32_t rxq_drops;
  uint32_t rx_alloc_failures;
  uint32_t rx_read_failures;
  uint32_t reorder_overflow;
  uint32_t reorder_timeouts;
  uint32_t reorder_outdated;
  uint32_t reorder_retransmit;
  uint32_t hw_restarts;
};

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

static void WiFiEvent(WiFiEvent_t event) {
  switch (event) {
    case ARDUINO_EVENT_WIFI_AP_START:
      Serial.println("[BASE] Mac-facing WiFi AP started");
      break;
    case ARDUINO_EVENT_WIFI_AP_STACONNECTED:
      Serial.println("[BASE] Mac connected to WiFi AP");
      break;
    case ARDUINO_EVENT_WIFI_AP_STAIPASSIGNED:
      Serial.println("[BASE] assigned an IP address to Mac");
      break;
    case ARDUINO_EVENT_WIFI_AP_STADISCONNECTED:
      Serial.println("[BASE] Mac disconnected from WiFi AP");
      break;
    default:
      break;
  }
}

static bool prepare_rate_history(const struct mmwlan_rc_stats *stats,
                                 bool *primed) {
  *primed = false;
  if (stats->n_entries == previous_rate_entries &&
      previous_sent != nullptr && previous_success != nullptr) {
    return true;
  }

  uint32_t *new_sent = static_cast<uint32_t *>(
      calloc(stats->n_entries, sizeof(uint32_t)));
  uint32_t *new_success = static_cast<uint32_t *>(
      calloc(stats->n_entries, sizeof(uint32_t)));
  if (new_sent == nullptr || new_success == nullptr) {
    free(new_sent);
    free(new_success);
    return false;
  }

  memcpy(new_sent, stats->total_sent,
         stats->n_entries * sizeof(uint32_t));
  memcpy(new_success, stats->total_success,
         stats->n_entries * sizeof(uint32_t));
  free(previous_sent);
  free(previous_success);
  previous_sent = new_sent;
  previous_success = new_success;
  previous_rate_entries = stats->n_entries;
  *primed = true;
  return true;
}

static bool sample_rate(halow_metrics::RateIntervalSummary *summary) {
  struct mmwlan_rc_stats *stats = mmwlan_get_rc_stats();
  if (stats == nullptr) {
    if (!reported_rate_unavailable) {
      Serial.println("HALOW_MONITOR_STATUS,source=rc_stats,state=unavailable");
      reported_rate_unavailable = true;
    }
    return false;
  }

  reported_rate_unavailable = false;
  bool available = true;
  if (stats->n_entries == 0 || stats->rate_info == nullptr ||
      stats->total_sent == nullptr || stats->total_success == nullptr) {
    Serial.println("HALOW_MONITOR_ERROR,source=rc_stats,reason=invalid_shape");
    available = false;
  } else {
    bool primed = false;
    if (!prepare_rate_history(stats, &primed)) {
      Serial.println("HALOW_MONITOR_ERROR,source=rc_stats,reason=allocation_failed");
      available = false;
    } else if (primed) {
      *summary = {false, 0, 0, -1, 0, -1};
    } else {
      *summary = halow_metrics::summarize_rate_interval(
          stats->rate_info,
          stats->total_sent,
          stats->total_success,
          previous_sent,
          previous_success,
          stats->n_entries);
      memcpy(previous_sent, stats->total_sent,
             stats->n_entries * sizeof(uint32_t));
      memcpy(previous_success, stats->total_success,
             stats->n_entries * sizeof(uint32_t));
    }
  }

  mmwlan_free_rc_stats(stats);
  return available;
}

static bool sample_umac(UmacInterval *interval) {
  struct mmwlan_stats_umac_data current = {};
  if (mmwlan_get_umac_stats(&current) != MMWLAN_SUCCESS) {
    Serial.println("HALOW_MONITOR_ERROR,source=umac_stats,reason=unavailable");
    return false;
  }

  if (!have_previous_umac) {
    *interval = {};
    previous_umac = current;
    have_previous_umac = true;
    interval->hw_restarts = current.hw_restart_counter;
    return true;
  }

  interval->radio_active = halow_metrics::radio_activity_changed(
      current.last_tx_time,
      previous_umac.last_tx_time,
      have_previous_umac);
  interval->txq_drops = halow_metrics::counter_delta(
      current.datapath_txq_frames_dropped,
      previous_umac.datapath_txq_frames_dropped);
  interval->rxq_drops = halow_metrics::counter_delta(
      current.datapath_rxq_frames_dropped,
      previous_umac.datapath_rxq_frames_dropped);
  interval->rx_alloc_failures = halow_metrics::counter_delta(
      current.datapath_driver_rx_alloc_failures,
      previous_umac.datapath_driver_rx_alloc_failures);
  interval->rx_read_failures = halow_metrics::counter_delta(
      current.datapath_driver_rx_read_failures,
      previous_umac.datapath_driver_rx_read_failures);
  interval->reorder_overflow = halow_metrics::counter_delta(
      current.datapath_rx_reorder_overflow,
      previous_umac.datapath_rx_reorder_overflow);
  interval->reorder_timeouts = halow_metrics::counter_delta(
      current.datapath_rx_reorder_timedout,
      previous_umac.datapath_rx_reorder_timedout);
  interval->reorder_outdated = halow_metrics::counter_delta(
      current.datapath_rx_reorder_outdated_drops,
      previous_umac.datapath_rx_reorder_outdated_drops);
  interval->reorder_retransmit = halow_metrics::counter_delta(
      current.datapath_rx_reorder_retransmit_drops,
      previous_umac.datapath_rx_reorder_retransmit_drops);
  interval->hw_restarts = current.hw_restart_counter;
  previous_umac = current;
  return true;
}

static void emit_metric(uint32_t now, uint32_t sample_ms) {
  halow_metrics::RateIntervalSummary rate = {false, 0, 0, -1, 0, -1};
  UmacInterval umac = {};
  const bool rate_available = sample_rate(&rate);
  if (!sample_umac(&umac)) {
    return;
  }

  struct mmwlan_ap_sta_status stations[MMWLAN_AP_MAX_STAS_LIMIT] = {};
  const uint8_t halow_clients = HaLow.getStationList(
      stations, MMWLAN_AP_MAX_STAS_LIMIT);
  halow_traffic::Snapshot traffic_totals = {0, 0};
  const bool traffic_counters_available = snapshot_halow_traffic(
      &traffic_totals);

  const double delivery = rate.traffic
      ? (100.0 * static_cast<double>(rate.successes) /
         static_cast<double>(rate.attempts))
      : -1.0;
  Serial.printf(
      "HALOW_METRIC,v=3,uptime_ms=%lu,sample_ms=%lu,rate_available=%u,"
      "radio_active=%u,halow_clients=%u,traffic_counters_available=%u,"
      "halow_tx_bytes_total=%llu,halow_rx_bytes_total=%llu,traffic=%u,"
      "mcs=%d,bw_mhz=%u,sgi=%d,tx_attempts=%lu,tx_success=%lu,"
      "delivery_pct=%.3f,txq_drops=%lu,rxq_drops=%lu,"
      "rx_alloc_failures=%lu,rx_read_failures=%lu,"
      "reorder_overflow=%lu,reorder_timeouts=%lu,reorder_outdated=%lu,"
      "reorder_retransmit=%lu,hw_restarts=%lu\n",
      static_cast<unsigned long>(now),
      static_cast<unsigned long>(sample_ms),
      rate_available ? 1U : 0U,
      umac.radio_active ? 1U : 0U,
      static_cast<unsigned int>(halow_clients),
      traffic_counters_available ? 1U : 0U,
      static_cast<unsigned long long>(traffic_totals.tx_bytes_total),
      static_cast<unsigned long long>(traffic_totals.rx_bytes_total),
      rate.traffic ? 1U : 0U,
      static_cast<int>(rate.mcs),
      static_cast<unsigned int>(rate.bandwidth_mhz),
      static_cast<int>(rate.sgi),
      static_cast<unsigned long>(rate.attempts),
      static_cast<unsigned long>(rate.successes),
      delivery,
      static_cast<unsigned long>(umac.txq_drops),
      static_cast<unsigned long>(umac.rxq_drops),
      static_cast<unsigned long>(umac.rx_alloc_failures),
      static_cast<unsigned long>(umac.rx_read_failures),
      static_cast<unsigned long>(umac.reorder_overflow),
      static_cast<unsigned long>(umac.reorder_timeouts),
      static_cast<unsigned long>(umac.reorder_outdated),
      static_cast<unsigned long>(umac.reorder_retransmit),
      static_cast<unsigned long>(umac.hw_restarts));
}

void setup() {
  Serial.begin(115200);

#ifdef HT_RC3268
  pinMode(HALOW_LDO_CTRL, OUTPUT);
  digitalWrite(HALOW_LDO_CTRL, HALOW_LDO_ENABLE);
#endif

  if (!HaLow.config(halow_IP, halow_gateway, halow_subnet,
                    primaryDNS, secondaryDNS)) {
    Serial.println("[BASE] HaLow AP configuration failed");
  }

  HaLow.init(HALOW_REGION);
  HaLow.AP(HALOW_AP_SSID, HALOW_PASSWORD, HALOW_CHANNEL);
  while (HaLow.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }

  Serial.println();
  Serial.printf("[BASE] HaLow AP started: SSID=%s IP=%s MAC=%s\n",
                HALOW_AP_SSID,
                HaLow.localIP().toString().c_str(),
                HaLow.macAddress().c_str());

  if (install_halow_traffic_accounting(HaLow.netif())) {
    Serial.println("[BASE] HaLow interface traffic accounting enabled");
  } else {
    Serial.println("HALOW_MONITOR_ERROR,source=traffic_counters,reason=install_failed");
  }

  WiFi.onEvent(WiFiEvent);
  if (!WiFi.mode(WIFI_AP)) {
    Serial.println("[BASE] WiFi AP mode failed");
    return;
  }
  if (!WiFi.softAPConfig(mac_AP_IP, mac_AP_gateway, mac_AP_subnet)) {
    Serial.println("[BASE] Mac-facing AP IP configuration failed");
    return;
  }
  if (!WiFi.softAP(BASE_AP_SSID, BASE_AP_PASSWORD, BASE_AP_CHANNEL)) {
    Serial.println("[BASE] Mac-facing WiFi AP failed");
    return;
  }
  Serial.printf("[BASE] Mac-facing AP ready: SSID=%s IP=%s\n",
                BASE_AP_SSID,
                WiFi.softAPIP().toString().c_str());

  ip_napt_enable(halow_IP, 1);
  Serial.println("[BASE] NAPT enabled on HaLow AP");

  netif_set_default(HaLow.netif());
  Serial.println("[BASE] default route set to HaLow netif");

  last_metric_ms = millis();
  last_status_ms = last_metric_ms;
}

void loop() {
  const uint32_t now = millis();
  const uint32_t metric_elapsed = now - last_metric_ms;
  if (metric_elapsed >= 1000) {
    last_metric_ms = now;
    emit_metric(now, metric_elapsed);
  }

  if (now - last_status_ms >= 5000) {
    last_status_ms = now;
    struct mmwlan_ap_sta_status stations[4];
    uint8_t halow_clients = HaLow.getStationList(stations, 4);
    Serial.printf("[BASE time=%u s] Mac clients=%u HaLow clients=%u\n",
                  now / 1000,
                  WiFi.softAPgetStationNum(),
                  halow_clients);
  }
  delay(5);
}
