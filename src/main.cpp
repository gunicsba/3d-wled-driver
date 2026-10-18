/*
 * XMasTractor: Christmas lights on the AgIsoAuxInputs brain board.
 *
 * The same firmware runs on every module (4-5 of them). Each module joins the
 * home WiFi (src/secrets.h), names itself xmas-<last 2 MAC bytes> (renameable
 * on its web page, e.g. "roof", "tree") and stores its LED count in NVS.
 *
 * It exposes a small subset of WLED's JSON API (/json/info, /json/state) so
 * MariMapper's WLED backend can light single LEDs by index, one module at a
 * time: marimapper --backend wled --server <module ip>.
 */
#include <Adafruit_NeoPixel.h>
#include <ArduinoJson.h>
#include <ArduinoOTA.h>
#include <ESP2SOTA.h>
#include <ESPmDNS.h>
#include <esp_system.h>
#include <Preferences.h>
#include <WebServer.h>
#include <WiFi.h>
#include <WiFiMulti.h>
#include <WiFiUdp.h>

#include "secrets.h"

constexpr uint8_t LED_PIN = 7;                  // brain board header H7
constexpr uint16_t MAX_LEDS = 1000;
constexpr uint16_t DEFAULT_LEDS = 50;
constexpr neoPixelType LED_TYPE = NEO_GRB + NEO_KHZ800; // default; changeable on the web page

static Adafruit_NeoPixel strip(DEFAULT_LEDS, LED_PIN, LED_TYPE);
struct ColorOrder { const char *name; neoPixelType type; };
static const ColorOrder ORDERS[] = {{"GRB", NEO_GRB}, {"RGB", NEO_RGB}, {"BRG", NEO_BRG},
                                    {"RBG", NEO_RBG}, {"GBR", NEO_GBR}, {"BGR", NEO_BGR}};
static String colorOrder = "GRB";

static void applyColorOrder() {
  for (const ColorOrder &o : ORDERS)
    if (colorOrder == o.name) strip.updateType(o.type + NEO_KHZ800);
}

static WebServer server(80);
static WiFiMulti wifiMulti;           // joins the strongest known network
static uint32_t lastWifiTry = 0;
static Preferences prefs;

enum Mode { MODE_OFF, MODE_RAINBOW, MODE_SOLID, MODE_TWINKLE, MODE_MANUAL, MODE_KNIGHT, MODE_CHASE,
            MODE_CANDY, MODE_FIRE, MODE_BREATHE, MODE_WIPE };
static const char *const MODE_NAMES[] = {"off", "rainbow", "solid", "twinkle", "manual", "knight",
                                         "chase", "candy", "fire", "breathe", "wipe"};
static uint8_t speed = 128; // 1-255, higher = faster
static Mode mode = MODE_RAINBOW;
static uint32_t solidColor = 0xFF0000;
static uint8_t brightness = 80;
static bool dirty = false;
static uint32_t effectStart = 0;
static String hostname;
static uint16_t numLeds = DEFAULT_LEDS;

// Boot light show: red blink (every 10th LED) until WiFi joins or a phone joins
// our AP, then green for a while, then the saved effect runs.
enum Boot { BOOT_WAIT, BOOT_OK, BOOT_RUN };
static Boot boot = BOOT_WAIT;
static bool skipBoot = false;          // checkbox on the web page, saved in NVS
static uint32_t bootT0 = 0, bootOkAt = 0;
constexpr uint32_t BOOT_TIMEOUT_MS = 45000; // no network at all: run the effect anyway
constexpr uint32_t BOOT_GREEN_MS = 3000;
static uint32_t saveAt = 0;            // debounced save of the current effect settings
static bool mdnsRestart = false;

// Realtime pixel data from a PC (WLED-compatible): UDP notifier protocol on
// 21324 (WARLS/DRGB/DRGBW/DNRGB) and DDP on 4048. While frames keep arriving
// they replace the effect; after the timeout the saved effect resumes.
static WiFiUDP udpRealtime, udpDdp;
static uint32_t realtimeUntil = 0; // 0 = not live
static uint32_t realtimePackets = 0;
// Live stats for /status, over the last full second: frames received, strip updates, longest gap.
static uint32_t rtLastPacket = 0, rtWinStart = 0, rtWinPackets = 0, rtWinShows = 0, rtWinMaxGap = 0;
static uint32_t rtFps = 0, rtShowFps = 0, rtMaxGap = 0;
// WiFi history for /status: how often the connection to the router dropped, why, and when.
static uint32_t staDrops = 0, staLastDropMs = 0;
static uint8_t staLastReason = 0;
constexpr uint32_t REALTIME_TIMEOUT_MS = 2500;

static bool realtimeActive() { return realtimeUntil && (int32_t)(millis() - realtimeUntil) < 0; }

static void markSave() { saveAt = millis() + 3000; if (!saveAt) saveAt = 1; }

static void saveSettings() {
  if (mode != MODE_MANUAL) prefs.putString("mode", MODE_NAMES[mode]);
  prefs.putUInt("color", solidColor);
  prefs.putUChar("bri", brightness);
  prefs.putUChar("speed", speed);
}

static void loadSettings() {
  const String m = prefs.getString("mode", "rainbow");
  mode = MODE_RAINBOW;
  for (size_t k = 0; k < sizeof(MODE_NAMES) / sizeof(MODE_NAMES[0]); k++)
    if (m == MODE_NAMES[k] && k != MODE_MANUAL) mode = (Mode)k;
  solidColor = prefs.getUInt("color", solidColor);
  brightness = prefs.getUChar("bri", brightness);
  speed = prefs.getUChar("speed", speed);
  skipBoot = prefs.getBool("skipboot", false);
  colorOrder = prefs.getString("order", "GRB");
}

static uint32_t parseColor(JsonVariantConst v) {
  if (v.is<const char *>()) return strtoul(v.as<const char *>(), nullptr, 16);
  if (v.is<JsonArrayConst>()) {
    JsonArrayConst a = v.as<JsonArrayConst>();
    return ((uint32_t)(a[0] | 0) << 16) | ((uint32_t)(a[1] | 0) << 8) | (uint32_t)(a[2] | 0);
  }
  return v | 0;
}

static void clearAll() {
  strip.clear();
  dirty = true;
}

// WLED "seg.i": [idx, color, idx, color...] or [color, color, ...] from 0.
// A colour is "RRGGBB" or [r,g,b]. Parsed leniently.
static void applyIndividual(JsonArrayConst arr) {
  uint16_t pos = 0;
  for (size_t k = 0; k < arr.size(); k++) {
    JsonVariantConst item = arr[k];
    if (item.is<int>() && k + 1 < arr.size() &&
        (arr[k + 1].is<const char *>() || arr[k + 1].is<JsonArrayConst>())) {
      pos = item.as<int>();
      if (pos < numLeds) strip.setPixelColor(pos, parseColor(arr[k + 1]));
      pos++;
      k++;
      continue;
    }
    if (pos < numLeds) strip.setPixelColor(pos, parseColor(item));
    pos++;
  }
  dirty = true;
}

static void handleState() {
  if (server.method() == HTTP_POST && !server.arg("plain").isEmpty()) {
    JsonDocument doc;
    if (deserializeJson(doc, server.arg("plain"))) {
      server.send(400, "application/json", "{\"error\":\"bad json\"}");
      return;
    }
    if (doc["bri"].is<int>()) {
      brightness = doc["bri"];
      strip.setBrightness(brightness);
      dirty = true;
    }
    if (doc["on"].is<bool>() && !doc["on"].as<bool>()) {
      mode = MODE_OFF;
      clearAll();
    }
    boot = BOOT_RUN;
    auto handle = [](JsonObjectConst s) {
      mode = MODE_MANUAL; // a segment command means an external client is driving
      if (s["i"].is<JsonArrayConst>()) applyIndividual(s["i"]);
      else if (s["col"].is<JsonArrayConst>() || s["on"].is<bool>()) clearAll(); // MariMapper's reset
    };
    JsonVariantConst seg = doc["seg"];
    if (seg.is<JsonObjectConst>()) handle(seg.as<JsonObjectConst>());
    else if (seg.is<JsonArrayConst>())
      for (JsonObjectConst s : seg.as<JsonArrayConst>()) handle(s);
  }
  JsonDocument out;
  out["on"] = mode != MODE_OFF;
  out["bri"] = brightness;
  out["seg"].to<JsonArray>().add<JsonObject>()["len"] = numLeds;
  String body;
  serializeJson(out, body);
  server.send(200, "application/json", body);
}

static void handleInfo() {
  JsonDocument doc;
  doc["ver"] = "0.14.0-xmastractor";
  doc["name"] = hostname;
  doc["leds"]["count"] = numLeds;
  String body;
  serializeJson(doc, body);
  server.send(200, "application/json", body);
}

static void redirectHome() {
  server.sendHeader("Location", "/");
  server.send(303);
}

static void handleSet() { // /set?mode=<name>&color=RRGGBB&bri=0-255&speed=1-255
  const String m = server.arg("mode");
  boot = BOOT_RUN;
  for (size_t k = 0; k < sizeof(MODE_NAMES) / sizeof(MODE_NAMES[0]); k++) {
    if (m == MODE_NAMES[k] && k != MODE_MANUAL) {
      mode = (Mode)k;
      clearAll();
      effectStart = millis();
    }
  }
  if (server.hasArg("color")) solidColor = strtoul(server.arg("color").c_str(), nullptr, 16);
  if (server.hasArg("bri")) {
    brightness = server.arg("bri").toInt();
    strip.setBrightness(brightness);
    dirty = true;
  }
  if (server.hasArg("speed")) speed = constrain(server.arg("speed").toInt(), 1, 255);
  if (server.hasArg("order")) {
    colorOrder = server.arg("order");
    prefs.putString("order", colorOrder);
    applyColorOrder();
    clearAll();
  }
  if (server.hasArg("skipboot")) {
    skipBoot = server.arg("skipboot") == "1";
    prefs.putBool("skipboot", skipBoot);
  }
  markSave();
  server.send(204);
}

static bool validHostname(const String &n) {
  if (n.isEmpty() || n.length() > 32 || n[0] == '-' || n[n.length() - 1] == '-') return false;
  for (char c : n)
    if (!(isdigit((unsigned char)c) || (c >= 'a' && c <= 'z') || c == '-')) return false;
  return true;
}

// Saves name / LED count; the name needs a reboot to take effect.
static void handleConfig() {
  String name = server.arg("name");
  name.trim();
  name.toLowerCase();
  const long leds = server.arg("leds").toInt();
  if (leds >= 1 && leds <= MAX_LEDS) {
    prefs.putUShort("leds", leds);
    numLeds = leds;
    strip.updateLength(numLeds);
    clearAll();
  }
  if (validHostname(name) && name != hostname) {
    prefs.putString("name", name);
    server.send(200, "text/html", "Saved. Rebooting as " + name + ".local ...");
    delay(500);
    ESP.restart();
    return;
  }
  redirectHome();
}

static const char PAGE[] PROGMEM = R"HTML(<!doctype html><html><head>
<meta name='viewport' content='width=device-width,initial-scale=1'><title>XMasTractor</title>
<style>body{font-family:sans-serif;max-width:480px;margin:1em auto;padding:0 1em}
button{margin:3px;padding:10px 14px;font-size:1em;border-radius:6px;border:1px solid #888}
button.on{background:#2a7;color:#fff}input[type=range]{width:100%}label{display:block;margin-top:.8em}
input[type=color]{width:100%;height:44px}</style></head><body>
<h2 id=t></h2><p id=i></p><div id=m></div>
<label>Colour <input type=color id=c value='#ff0000'></label>
<label>Brightness <input type=range id=b min=1 max=255></label>
<label>Speed <input type=range id=s min=1 max=255></label>
<label><input type=checkbox id=sk> Skip the boot light show (go straight to the saved effect)</label>
<label>LED colour order <select id=o><option>GRB<option>RGB<option>BRG<option>RBG<option>GBR<option>BGR</select></label>
<h4>Module</h4>
<form action=/config>Name <input name=name id=n> LEDs <input name=leds id=l type=number min=1 max=1000 size=5>
<button>Save</button></form>
<p><a href=/update>Firmware update (upload a .bin)</a></p>
<p><small>MariMapper: <code id=mm></code></small></p>
<script>
const $=i=>document.getElementById(i),
 send=q=>fetch('/set?'+q).then(load);
function load(){fetch('/status').then(r=>r.json()).then(d=>{
 $('t').textContent=d.name;$('i').textContent=d.leds+' LEDs, '+d.ip+', AP: '+d.ap;$('n').value=d.name;$('l').value=d.leds;
 $('sk').checked=d.skipboot;$('o').value=d.order;$('mm').textContent='--backend wled --server '+d.ip;
 if(document.activeElement!=$('b'))$('b').value=d.bri;if(document.activeElement!=$('s'))$('s').value=d.speed;
 $('m').innerHTML='';d.modes.forEach(n=>{const e=document.createElement('button');e.textContent=n;
  if(n==d.mode)e.className='on';e.onclick=()=>send('mode='+n);$('m').appendChild(e)})})}
$('c').onchange=e=>send('color='+e.target.value.slice(1));
$('o').onchange=e=>send('order='+e.target.value);
$('sk').onchange=e=>send('skipboot='+(e.target.checked?1:0));
$('b').onchange=e=>send('bri='+e.target.value);$('s').onchange=e=>send('speed='+e.target.value);
load();
</script></body></html>)HTML";

static void handleRoot() { server.send_P(200, "text/html", PAGE); }

static void realtimeStart(uint32_t timeoutMs) {
  boot = BOOT_RUN;
  realtimeUntil = millis() + timeoutMs;
  if (!realtimeUntil) realtimeUntil = 1;
  realtimePackets++;
  const uint32_t now = millis();
  if (rtLastPacket && now - rtLastPacket > rtWinMaxGap) rtWinMaxGap = now - rtLastPacket;
  rtLastPacket = now;
  rtWinPackets++;
}

static void rtStatsTick() { // once per second
  const uint32_t now = millis();
  if (now - rtWinStart < 1000) return;
  rtWinStart = now;
  rtFps = rtWinPackets;
  rtShowFps = rtWinShows;
  rtMaxGap = rtWinMaxGap;
  rtWinPackets = rtWinShows = rtWinMaxGap = 0;
}

static void setRgb(uint32_t i, const uint8_t *rgb) {
  if (i < numLeds) strip.setPixelColor(i, rgb[0], rgb[1], rgb[2]);
}

static void pollRealtime() {
  static uint8_t buf[1500];
  int n;
  // Read everything that is waiting (the newest packet is applied last, so it wins): after a stall
  // the old frames must not be replayed one per loop pass.
  for (int guard = 0; guard < 32 && (n = udpRealtime.parsePacket()) > 0; guard++) {
    if (n <= 2) { udpRealtime.read(buf, sizeof(buf)); continue; }
    n = udpRealtime.read(buf, sizeof(buf));
    const uint8_t proto = buf[0];
    const uint32_t timeoutMs = buf[1] == 255 ? 3600000UL : buf[1] * 1000UL;
    if (proto >= 1 && proto <= 4) {
      realtimeStart(timeoutMs ? timeoutMs : REALTIME_TIMEOUT_MS);
      if (proto == 1) { // WARLS: [index, r, g, b]*
        for (int k = 2; k + 3 < n; k += 4) setRgb(buf[k], &buf[k + 1]);
      } else if (proto == 2 || proto == 3) { // DRGB / DRGBW from LED 0
        const int step = proto == 2 ? 3 : 4;
        for (int k = 2, i = 0; k + 2 < n; k += step, i++) setRgb(i, &buf[k]);
      } else if (n > 4) { // DNRGB: start index (16 bit) then RGB
        uint32_t i = (buf[2] << 8) | buf[3];
        for (int k = 4; k + 2 < n; k += 3, i++) setRgb(i, &buf[k]);
      }
      dirty = true;
    }
  }
  for (int guard = 0; guard < 32 && (n = udpDdp.parsePacket()) > 0; guard++) {
    if (n < 10) { udpDdp.read(buf, sizeof(buf)); continue; }
    n = udpDdp.read(buf, sizeof(buf));
    const int hdr = (buf[0] & 0x10) ? 14 : 10; // timecode flag adds 4 bytes
    const uint32_t offset = ((uint32_t)buf[4] << 24) | (buf[5] << 16) | (buf[6] << 8) | buf[7];
    const int len = (buf[8] << 8) | buf[9];
    if (n >= hdr + len) {
      realtimeStart(REALTIME_TIMEOUT_MS);
      for (int k = 0; k + 2 < len; k += 3) setRgb(offset / 3 + k / 3, &buf[hdr + k]);
      if (buf[0] & 0x01) dirty = true; // PUSH flag: frame complete
    }
  }
}

static void handleStatus() {
  JsonDocument doc;
  doc["name"] = hostname;
  doc["ip"] = WiFi.localIP().toString();
  doc["leds"] = numLeds;
  doc["bri"] = brightness;
  doc["speed"] = speed;
  doc["skipboot"] = skipBoot;
  doc["order"] = colorOrder;
  doc["live"] = realtimeActive();
  doc["packets"] = realtimePackets;
  doc["rt_fps"] = rtFps;           // frames received in the last second
  doc["show_fps"] = rtShowFps;     // strip updates in the last second
  doc["rt_gap_ms"] = rtMaxGap;     // longest wait between two frames in that second
  doc["ap"] = WiFi.softAPSSID();
  doc["wifi"] = WiFi.SSID();         // the network we joined
  doc["rssi"] = WiFi.RSSI();         // its signal strength, dBm (-50 great, -70 weak, -80 bad)
  doc["channel"] = WiFi.channel();
  doc["uptime_s"] = millis() / 1000;                        // goes back to a small number after a reboot
  doc["reset"] = (int)esp_reset_reason();                   // why the last boot happened: 1 power-on, 3 software, 4 crash, 5-7 watchdog, 9 brownout
  doc["heap"] = ESP.getFreeHeap();
  doc["sta_drops"] = staDrops;                              // times the router connection was lost since boot
  doc["sta_reason"] = staLastReason;                        // reason code of the last loss (2 auth expired, 200 beacon timeout, 201 no AP, 8 left)
  doc["sta_drop_ago_s"] = staDrops ? (millis() - staLastDropMs) / 1000 : -1;
  doc["mode"] = MODE_NAMES[mode];
  JsonArray modes = doc["modes"].to<JsonArray>();
  for (size_t k = 0; k < sizeof(MODE_NAMES) / sizeof(MODE_NAMES[0]); k++)
    if (k != MODE_MANUAL) modes.add(MODE_NAMES[k]);
  String body;
  serializeJson(doc, body);
  server.send(200, "application/json", body);
}

// Boot status: every 10th LED in colour (0 = all dark).
static void showEveryTenth(uint32_t color) {
  strip.clear();
  if (color)
    for (uint16_t k = 0; k < numLeds; k += 10) strip.setPixelColor(k, color);
  strip.show();
}

static void onWifiEvent(WiFiEvent_t event, WiFiEventInfo_t info) {
  if (event == ARDUINO_EVENT_WIFI_STA_GOT_IP) mdnsRestart = true;
  if (event == ARDUINO_EVENT_WIFI_STA_DISCONNECTED) {
    staDrops++;
    staLastReason = info.wifi_sta_disconnected.reason;
    staLastDropMs = millis();
  }
}

static void startWifi() {
  WiFi.mode(WIFI_AP_STA);
  WiFi.setSleep(false);            // modem sleep batches packets and stalls the stream
  WiFi.setHostname(hostname.c_str());
  WiFi.onEvent(onWifiEvent);
  WiFi.setAutoReconnect(true);
  WiFi.softAP(hostname.c_str(), AP_PASSWORD); // phones join this; page at 192.168.4.1
  for (const WifiCredential &n : WIFI_NETWORKS) wifiMulti.addAP(n.ssid, n.password);
  Serial.printf("AP '%s' at %s, known networks: %u\n", hostname.c_str(),
                WiFi.softAPIP().toString().c_str(), (unsigned)(sizeof(WIFI_NETWORKS) / sizeof(WIFI_NETWORKS[0])));
  showEveryTenth(0xFF0000);        // red while the first scan and join (up to ~10 s) runs
  wifiMulti.run(10000);            // scans and joins the strongest known network right away
}

static bool networkUp() { return WiFi.status() == WL_CONNECTED || WiFi.softAPgetStationNum() > 0; }

// Runs the boot light show without blocking the web server / OTA.
static void bootUpdate() {
  if (boot == BOOT_RUN) return;
  const uint32_t now = millis();
  static int lastShown = -1;
  if (boot == BOOT_WAIT) {
    if (networkUp()) {
      boot = BOOT_OK;
      bootOkAt = now;
      lastShown = -1;
    } else if (now - bootT0 > BOOT_TIMEOUT_MS) {
      boot = BOOT_RUN;
      clearAll();
      effectStart = now;
    } else {
      const int on = (now / 500) % 2;
      if (on != lastShown) {
        lastShown = on;
        showEveryTenth(on ? 0xFF0000 : 0);
      }
      return;
    }
  }
  if (boot == BOOT_OK) {
    if (lastShown != 2) {
      lastShown = 2;
      showEveryTenth(0x00FF00);
    }
    if (now - bootOkAt > BOOT_GREEN_MS) {
      boot = BOOT_RUN;
      clearAll();
      effectStart = now;
    }
  }
}

void setup() {
  Serial.begin(115200);
  prefs.begin("xmas", false);
  uint8_t mac[6];
  WiFi.macAddress(mac);
  char def[16];
  snprintf(def, sizeof(def), "xmas-%02x%02x", mac[4], mac[5]);
  hostname = prefs.getString("name", def);
  numLeds = prefs.getUShort("leds", DEFAULT_LEDS);
  loadSettings();

  strip.updateLength(numLeds);
  applyColorOrder();
  strip.begin();
  strip.setBrightness(brightness);
  strip.show();
  bootT0 = millis();
  if (skipBoot) boot = BOOT_RUN;
  startWifi();

  ArduinoOTA.setHostname(hostname.c_str());
  ArduinoOTA.begin();

  server.on("/", handleRoot);
  server.on("/set", handleSet);
  server.on("/status", handleStatus);
  server.on("/config", handleConfig);
  server.on("/json/info", handleInfo);
  server.on("/json/state", handleState);
  server.on("/json", handleState);
  ESP2SOTA.begin(&server); // web firmware upload at /update
  server.begin();
  udpRealtime.begin(21324);
  udpDdp.begin(4048);
}

static uint8_t sin8(uint32_t x) { return 128 + 127 * sinf((x & 255) * 6.2831853f / 256); }

static uint32_t scale(uint32_t c, uint8_t f) { // f/255 of colour c
  return ((((c >> 16) & 0xFF) * f / 255) << 16) | ((((c >> 8) & 0xFF) * f / 255) << 8) | ((c & 0xFF) * f / 255);
}

static void fadeAll(uint8_t keep) {
  for (uint16_t i = 0; i < numLeds; i++) strip.setPixelColor(i, scale(strip.getPixelColor(i), keep));
}

// Position 0..numLeds-1 advancing at `speed`, in 1/16 LED steps per ms scaled.
static uint32_t ticks() { return (millis() - effectStart) * (uint32_t)speed / 64; } // 1/16 LED units

static void animate() {
  if (boot != BOOT_RUN || realtimeActive()) return;
  static uint32_t last = 0;
  if (millis() - last < 20) return;
  last = millis();
  const uint32_t t = ticks();
  switch (mode) {
    case MODE_RAINBOW:
      for (uint16_t i = 0; i < numLeds; i++)
        strip.setPixelColor(i, strip.gamma32(strip.ColorHSV(t * 64 + i * 65536UL / numLeds)));
      break;
    case MODE_SOLID:
      strip.fill(solidColor);
      break;
    case MODE_TWINKLE:
      fadeAll(224);
      if (random(3) == 0) strip.setPixelColor(random(numLeds), strip.ColorHSV(random(65536), 150, 255));
      break;
    case MODE_KNIGHT: { // red eye (colour selectable) bouncing with a fading tail
      const uint32_t span = (uint32_t)(numLeds > 1 ? numLeds - 1 : 1) * 16;
      uint32_t p = t % (2 * span);
      if (p > span) p = 2 * span - p;
      const uint16_t head = p / 16;
      fadeAll(180);
      strip.setPixelColor(head, solidColor);
      if (head > 0) strip.setPixelColor(head - 1, scale(solidColor, 90));
      if (head + 1 < numLeds) strip.setPixelColor(head + 1, scale(solidColor, 90));
      break;
    }
    case MODE_CHASE: // theatre chase: every 3rd LED lit, marching
      for (uint16_t i = 0; i < numLeds; i++) strip.setPixelColor(i, (i + t / 16) % 3 == 0 ? solidColor : 0);
      break;
    case MODE_CANDY: // red / white stripes, marching
      for (uint16_t i = 0; i < numLeds; i++) strip.setPixelColor(i, ((i + t / 16) / 3) % 2 ? 0xFF0000 : 0xFFFFFF);
      break;
    case MODE_FIRE:
      for (uint16_t i = 0; i < numLeds; i++) {
        const uint8_t heat = random(80, 255);
        strip.setPixelColor(i, strip.Color(heat, heat / 4 + random(30), 0));
      }
      break;
    case MODE_BREATHE: {
      const uint8_t f = (sin8(t * 4) * (uint32_t)sin8(t * 4)) / 255; // smooth, longer dark
      strip.fill(scale(solidColor, f));
      break;
    }
    case MODE_WIPE: { // fill with the colour one LED at a time, then clear
      const uint32_t n = (t / 16) % (numLeds * 2);
      for (uint16_t i = 0; i < numLeds; i++) strip.setPixelColor(i, (n < numLeds ? i <= n : i > n - numLeds) ? solidColor : 0);
      break;
    }
    default:
      return;
  }
  dirty = true;
}

void loop() {
  ArduinoOTA.handle();
  server.handleClient();
  if (mdnsRestart) { // station got an IP: (re)announce on it
    mdnsRestart = false;
    MDNS.end();
    MDNS.begin(hostname.c_str());
    MDNS.addService("http", "tcp", 80);
    Serial.printf("IP %s  http://%s.local/\n", WiFi.localIP().toString().c_str(), hostname.c_str());
  }
  pollRealtime();
  static bool wasLive = false;
  if (wasLive && !realtimeActive()) { // frames stopped: back to the saved effect
    clearAll();
    effectStart = millis();
  }
  wasLive = realtimeActive();
  bootUpdate();
  animate();
  if (saveAt && (int32_t)(millis() - saveAt) >= 0) {
    saveAt = 0;
    saveSettings();
  }
  rtStatsTick();
  if (WiFi.status() != WL_CONNECTED && millis() - lastWifiTry > 12000) { // (re)join the best network we know
    lastWifiTry = millis();
    wifiMulti.run(8000);
  }
  if (dirty) {
    strip.show();
    rtWinShows++;
    dirty = false;
  }
}
