/**
 * Headless-Chrome CDP harness for index_latency.html.
 *
 * Connects to the page's DevTools target, polls the latency panel's DOM
 * values, and records console output plus uncaught exceptions.  This lets the
 * measurement be verified without a human watching the browser.
 *
 * Usage: node cdp_probe.mjs [runMs] [label]
 */

const CDP_HTTP = "http://127.0.0.1:9222";
const RUN_MS = Number(process.argv[2] || 30000);
const LABEL = process.argv[3] || "run";

const targets = await (await fetch(`${CDP_HTTP}/json/list`)).json();
const page = targets.find(
  (t) => t.type === "page" && String(t.url).includes("index_latency")
);

if (!page) {
  console.error("FAIL: no index_latency page target found");
  console.error(JSON.stringify(targets.map((t) => [t.type, t.url]), null, 2));
  process.exit(1);
}

const ws = new WebSocket(page.webSocketDebuggerUrl);
let nextId = 0;
const pending = new Map();
const events = [];

function send(method, params = {}) {
  const id = ++nextId;
  ws.send(JSON.stringify({ id, method, params }));
  return new Promise((resolve) => pending.set(id, resolve));
}

ws.addEventListener("message", (event) => {
  const message = JSON.parse(event.data);

  if (message.id && pending.has(message.id)) {
    pending.get(message.id)(message.result);
    pending.delete(message.id);
    return;
  }

  if (message.method === "Runtime.consoleAPICalled") {
    const text = message.params.args
      .map((a) => (a.value !== undefined ? a.value : a.description ?? a.type))
      .join(" ");
    events.push(`[console.${message.params.type}] ${text}`);
  }

  if (message.method === "Runtime.exceptionThrown") {
    const d = message.params.exceptionDetails;
    events.push(
      `[EXCEPTION] ${d.exception?.description || d.text || "unknown"}`
    );
  }
});

await new Promise((resolve) => ws.addEventListener("open", resolve, { once: true }));
await send("Runtime.enable");
await send("Log.enable");

const READ_PANEL = `JSON.stringify({
  conn: document.getElementById("connectionStatusText")?.textContent,
  peer: document.getElementById("peerConnection")?.textContent,
  ice: document.getElementById("iceConnection")?.textContent,
  fps: document.getElementById("fps")?.textContent,
  bitrate: document.getElementById("bitrate")?.textContent,
  resolution: document.getElementById("resolution")?.textContent,
  codec: document.getElementById("codec")?.textContent,
  latency: document.getElementById("softwareLatency")?.textContent,
  captureToCallback: document.getElementById("latencyCaptureCallback")?.textContent,
  captureToReceive: document.getElementById("latencyCaptureReceive")?.textContent,
  receiveToCallback: document.getElementById("latencyReceiveCallback")?.textContent,
  captureToDisplay: document.getElementById("latencyCaptureDisplay")?.textContent,
  processing: document.getElementById("latencyProcessing")?.textContent,
  rtpTimestamp: document.getElementById("latencyRtpTimestamp")?.textContent,
  windowStats: document.getElementById("latencyWindowStats")?.textContent,
  clockOffset: document.getElementById("latencyClockOffset")?.textContent,
  clockRtt: document.getElementById("latencyClockRtt")?.textContent,
  matchMiss: document.getElementById("latencyMatchStats")?.textContent,
  metadata: document.getElementById("latencyMetadata")?.textContent,
  videoWidth: document.getElementById("video")?.videoWidth,
  videoHeight: document.getElementById("video")?.videoHeight,
  videoPaused: document.getElementById("video")?.paused,
  readyState: document.getElementById("video")?.readyState
})`;

const samples = [];
const started = Date.now();

while (Date.now() - started < RUN_MS) {
  const reply = await send("Runtime.evaluate", {
    expression: READ_PANEL,
    returnByValue: true,
  });

  try {
    samples.push({
      t: Math.round((Date.now() - started) / 100) / 10,
      ...JSON.parse(reply.result.value),
    });
  } catch {
    samples.push({ t: Date.now() - started, readError: true });
  }

  await new Promise((resolve) => setTimeout(resolve, 2000));
}

console.log(`=== SAMPLES (${LABEL}) ===`);
for (const sample of samples) {
  console.log(JSON.stringify(sample));
}

console.log(`=== CONSOLE / EXCEPTIONS (${LABEL}, last 40) ===`);
console.log(events.slice(-40).join("\n") || "(none)");

const exceptions = events.filter((e) => e.startsWith("[EXCEPTION]"));
console.log(`=== EXCEPTION COUNT: ${exceptions.length} ===`);

ws.close();

// The DevTools WebSocket keeps the event loop alive even after close().
process.exit(0);
