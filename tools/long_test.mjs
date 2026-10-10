/**
 * Long-duration measurement sampler.
 *
 * Reads the latency panel every few seconds through the Chrome DevTools
 * Protocol and appends one JSON record per sample, so a multi-hour soak can be
 * analysed afterwards instead of watched.
 *
 * Survives the page reloading itself (session recovery) by re-resolving the
 * CDP target and reconnecting when an evaluation fails.
 *
 * Usage: node long_test.mjs <durationMs> <sampleMs> <outFile>
 */

import fs from "node:fs";

const DURATION_MS = Number(process.argv[2] || 3600000);
const SAMPLE_MS = Number(process.argv[3] || 5000);
const OUT_FILE = process.argv[4] || "long-test.jsonl";

const CDP_HTTP = "http://127.0.0.1:9222";

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
  windowStats: document.getElementById("latencyWindowStats")?.textContent,
  clockOffset: document.getElementById("latencyClockOffset")?.textContent,
  clockRtt: document.getElementById("latencyClockRtt")?.textContent,
  matchMiss: document.getElementById("latencyMatchStats")?.textContent,
  metadata: document.getElementById("latencyMetadata")?.textContent,
  packetsReceived: document.getElementById("packetsReceived")?.textContent,
  packetsLost: document.getElementById("packetsLost")?.textContent,
  packetLoss: document.getElementById("packetLoss")?.textContent,
  jitter: document.getElementById("jitter")?.textContent,
  rtt: document.getElementById("rtt")?.textContent,
  videoWidth: document.getElementById("video")?.videoWidth,
  videoHeight: document.getElementById("video")?.videoHeight,
  readyState: document.getElementById("video")?.readyState
})`;

const stream = fs.createWriteStream(OUT_FILE, { flags: "a" });

let socket = null;
let nextId = 0;
const pending = new Map();
let connected = false;
let exceptionCount = 0;

function stamp() {
  return new Date().toISOString();
}

function emit(record) {
  stream.write(JSON.stringify(record) + "\n");
}

async function connect() {
  const targets = await (await fetch(`${CDP_HTTP}/json/list`)).json();

  const page = targets.find(
    (t) => t.type === "page" && String(t.url).includes("index_latency")
  );

  if (!page) {
    return false;
  }

  socket = new WebSocket(page.webSocketDebuggerUrl);

  socket.addEventListener("message", (event) => {
    const message = JSON.parse(event.data);

    if (message.id && pending.has(message.id)) {
      pending.get(message.id)(message.result);
      pending.delete(message.id);
      return;
    }

    if (message.method === "Runtime.exceptionThrown") {
      exceptionCount++;
      const details = message.params.exceptionDetails;
      emit({
        t: Date.now(),
        kind: "exception",
        detail:
          details?.exception?.description || details?.text || "unknown",
      });
    }
  });

  await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("cdp open timeout")), 5000);
    socket.addEventListener(
      "open",
      () => {
        clearTimeout(timer);
        resolve();
      },
      { once: true }
    );
    socket.addEventListener(
      "error",
      () => {
        clearTimeout(timer);
        reject(new Error("cdp socket error"));
      },
      { once: true }
    );
  });

  await send("Runtime.enable");
  connected = true;
  return true;
}

function send(method, params = {}) {
  const id = ++nextId;
  socket.send(JSON.stringify({ id, method, params }));
  return new Promise((resolve) => pending.set(id, resolve));
}

async function takeSample() {
  if (!connected) {
    try {
      await connect();
      console.log(stamp(), "cdp reconnected");
    } catch {
      return { sampleError: true, reason: "no cdp target" };
    }
  }

  try {
    const reply = await send("Runtime.evaluate", {
      expression: READ_PANEL,
      returnByValue: true,
    });

    return JSON.parse(reply.result.value);
  } catch {
    connected = false;
    try {
      socket.close();
    } catch {
      /* already gone */
    }
    return { sampleError: true, reason: "evaluate failed" };
  }
}

const startedAt = Date.now();
let samples = 0;

console.log(
  stamp(),
  `sampling every ${SAMPLE_MS / 1000}s for ${DURATION_MS / 60000} min into ${OUT_FILE}`
);

while (Date.now() - startedAt < DURATION_MS) {
  const panel = await takeSample();
  samples++;

  emit({
    t: Date.now(),
    elapsedS: Math.round((Date.now() - startedAt) / 1000),
    ...panel,
  });

  if (samples % 60 === 0) {
    console.log(
      stamp(),
      `progress ${Math.round((Date.now() - startedAt) / 1000)}s ` +
        `samples=${samples} exceptions=${exceptionCount}`
    );
  }

  await new Promise((resolve) => setTimeout(resolve, SAMPLE_MS));
}

console.log(stamp(), `finished samples=${samples} exceptions=${exceptionCount}`);
stream.end();
process.exit(0);
