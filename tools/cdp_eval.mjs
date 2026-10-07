/**
 * Minimal CDP one-shot evaluator.
 *
 * Evaluates one expression in the page that hosts index_latency.html and
 * prints the CDP result as JSON.  Used for things like reloading the page
 * before restarting the sender.
 *
 * Usage: node cdp_eval.mjs "location.reload()"
 */

const CDP_HTTP = "http://127.0.0.1:9222";
const expression = process.argv[2];

if (!expression) {
  console.error("usage: node cdp_eval.mjs <expression>");
  process.exit(2);
}

const targets = await (await fetch(`${CDP_HTTP}/json/list`)).json();
const page = targets.find(
  (t) => t.type === "page" && String(t.url).includes("index_latency")
);

if (!page) {
  console.error("FAIL: no index_latency page target found");
  process.exit(1);
}

const ws = new WebSocket(page.webSocketDebuggerUrl);
let nextId = 0;
const pending = new Map();

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
  }
});

await new Promise((resolve) =>
  ws.addEventListener("open", resolve, { once: true })
);

await send("Runtime.enable");

const reply = await send("Runtime.evaluate", {
  expression,
  returnByValue: true,
});

console.log(JSON.stringify(reply));

ws.close();
process.exit(0);
