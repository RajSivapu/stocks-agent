const HELP = "Try /buy, /sell, /stop, /portfolio, or /help.";
const BUCKETS = new Set(["core", "growth", "speculative"]);
const NUMBER = /^(?:\d+(?:,\d{3})*(?:\.\d+)?|\d*\.\d+)$/;
const PRICE = /^\$?(?:\d+(?:,\d{3})*(?:\.\d+)?|\d*\.\d+)$/;
const TICKER = /^[A-Z][A-Z0-9]*(?:[.-][A-Z0-9]+)*$/;
const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

function reject() {
  return { ok: false, error: HELP };
}

function positiveNumber(token, { price = false } = {}) {
  if (typeof token !== "string" || !(price ? PRICE : NUMBER).test(token)) return null;
  const value = Number(token.replaceAll("$", "").replaceAll(",", ""));
  return Number.isFinite(value) && value > 0 ? value : null;
}

function normalizedTicker(token) {
  if (typeof token !== "string") return null;
  const ticker = token.toUpperCase();
  return ticker.length <= 12 && TICKER.test(ticker) ? ticker : null;
}

function tradeDate(token) {
  if (token === undefined) return undefined;
  if (typeof token !== "string" || !ISO_DATE.test(token) || token < "2000-01-01") return null;
  const parsed = new Date(`${token}T00:00:00Z`);
  return Number.isNaN(parsed.valueOf()) || parsed.toISOString().slice(0, 10) !== token ? null : token;
}

function buy(tickerToken, qtyToken, priceToken, bucketToken, dateToken) {
  const ticker = normalizedTicker(tickerToken);
  const qty = positiveNumber(qtyToken);
  const price = positiveNumber(priceToken, { price: true });
  const bucket = bucketToken?.toLowerCase() ?? null;
  const executedOn = tradeDate(dateToken);
  if (!ticker || qty === null || price === null || (bucket !== null && !BUCKETS.has(bucket)) || executedOn === null) return reject();
  return {
    ok: true,
    command: {
      operation: "buy", ticker, qty, price, bucket,
      ...(executedOn ? { executed_on: executedOn } : {}),
    },
  };
}

function sell(tickerToken, qtyToken, priceToken, dateToken) {
  const ticker = normalizedTicker(tickerToken);
  const qty = qtyToken?.toLowerCase() === "all" ? "all" : positiveNumber(qtyToken);
  const price = positiveNumber(priceToken, { price: true });
  const executedOn = tradeDate(dateToken);
  if (!ticker || qty === null || qty === undefined || price === null || executedOn === null) return reject();
  return {
    ok: true,
    command: {
      operation: "sell", ticker, qty, price,
      ...(executedOn ? { executed_on: executedOn } : {}),
    },
  };
}

function stop(tickerToken, stopToken) {
  const ticker = normalizedTicker(tickerToken);
  const stopValue = positiveNumber(stopToken, { price: true });
  if (!ticker || stopValue === null) return reject();
  return { ok: true, command: { operation: "stop", ticker, stop: stopValue } };
}

export function parsePortfolioCommand(input) {
  if (typeof input !== "string") return reject();
  const text = input.trim().replace(/\s+/g, " ");
  if (!text) return reject();

  if (/^\/portfolio(?:@[A-Za-z0-9_]+)?$/i.test(text)) return { ok: true, command: { operation: "portfolio" } };
  if (/^\/help(?:@[A-Za-z0-9_]+)?$/i.test(text)) return { ok: true, command: { operation: "help" } };

  let match = text.match(/^\/buy(?:@[A-Za-z0-9_]+)? (\S+) (\S+) (\S+)(?: (\S+))? on (\S+)$/i);
  if (match) return buy(match[1], match[2], match[3], match[4], match[5]);
  match = text.match(/^bought (\S+) (\S+) (?:at|@) (\S+)(?: (\S+))? on (\S+)$/i);
  if (match) return buy(match[2], match[1], match[3], match[4], match[5]);
  match = text.match(/^\/buy(?:@[A-Za-z0-9_]+)? (\S+) (\S+) (\S+)(?: (\S+))?$/i);
  if (match) return buy(match[1], match[2], match[3], match[4]);
  match = text.match(/^bought (\S+) (\S+) (?:at|@) (\S+)(?: (\S+))?$/i);
  if (match) return buy(match[2], match[1], match[3], match[4]);

  match = text.match(/^\/sell(?:@[A-Za-z0-9_]+)? (\S+) (\S+) (\S+) on (\S+)$/i);
  if (match) return sell(match[1], match[2], match[3], match[4]);
  match = text.match(/^sold (\S+) (\S+) (?:at|@) (\S+) on (\S+)$/i);
  if (match) return sell(match[2], match[1], match[3], match[4]);
  match = text.match(/^\/sell(?:@[A-Za-z0-9_]+)? (\S+) (\S+) (\S+)$/i);
  if (match) return sell(match[1], match[2], match[3]);
  match = text.match(/^sold (\S+) (\S+) (?:at|@) (\S+)$/i);
  if (match) return sell(match[2], match[1], match[3]);

  match = text.match(/^\/stop(?:@[A-Za-z0-9_]+)? (\S+) (\S+)$/i);
  if (match) return stop(match[1], match[2]);
  match = text.match(/^move (\S+) stop to (\S+)$/i);
  if (match) return stop(match[1], match[2]);

  return reject();
}
