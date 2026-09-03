const HELP = "Try /start, /status, /unlink, /buy, /sell, /stop, /portfolio, /plan, /cancelplan, /plans, or /help.";
const BUCKETS = new Set(["core", "growth", "speculative"]);
const NUMBER = /^(?:\d+(?:,\d{3})*(?:\.\d+)?|\d*\.\d+)$/;
const PRICE = /^\$?(?:\d+(?:,\d{3})*(?:\.\d+)?|\d*\.\d+)$/;
const TICKER = /^[A-Z][A-Z0-9]*(?:[.-][A-Z0-9]+)*$/;
const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;
const PAIRING_CODE = /^[A-HJ-NP-Z2-9]{10}$/;

function reject() {
  return { ok: false, error: HELP };
}

function positiveDecimal(token, { price = false, scale = 4, maxWhole = 1000000 } = {}) {
  if (typeof token !== "string" || !(price ? PRICE : NUMBER).test(token)) return null;
  const normalized = token.replaceAll("$", "").replaceAll(",", "");
  const [wholeRaw, fractionRaw = ""] = normalized.split(".");
  if (fractionRaw.length > scale) return null;
  const whole = (wholeRaw || "0").replace(/^0+(?=\d)/, "");
  const fraction = fractionRaw.replace(/0+$/, "");
  if (BigInt(whole) > BigInt(maxWhole) || (BigInt(whole) === BigInt(maxWhole) && fraction)) return null;
  const value = fraction ? `${whole}.${fraction}` : whole;
  return /^0(?:\.0*)?$/.test(value) ? null : value;
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
  const qty = positiveDecimal(qtyToken, { scale: 8 });
  const price = positiveDecimal(priceToken, { price: true, scale: 4 });
  const bucket = bucketToken?.toLowerCase() ?? null;
  const executedOn = tradeDate(dateToken);
  if (!ticker || qty === null || price === null || (bucket !== null && !BUCKETS.has(bucket)) || executedOn === null) return reject();
  return {
    ok: true,
    command: {
      operation: "buy", ticker, quantity: qty, fill_price: price,
      fees: "0", cash_total: null,
      ...(bucket ? { bucket } : {}),
      ...(executedOn ? { executed_on: executedOn } : {}),
    },
  };
}

function sell(tickerToken, qtyToken, priceToken, dateToken) {
  const ticker = normalizedTicker(tickerToken);
  const qty = qtyToken?.toLowerCase() === "all"
    ? "all"
    : positiveDecimal(qtyToken, { scale: 8 });
  const price = positiveDecimal(priceToken, { price: true, scale: 4 });
  const executedOn = tradeDate(dateToken);
  if (!ticker || qty === null || qty === undefined || price === null || executedOn === null) return reject();
  return {
    ok: true,
    command: {
      operation: qty === "all" ? "sell_all" : "sell", ticker,
      ...(qty === "all" ? {} : { quantity: qty }),
      fill_price: price, fees: "0", cash_total: null,
      ...(executedOn ? { executed_on: executedOn } : {}),
    },
  };
}

function stop(tickerToken, stopToken) {
  const ticker = normalizedTicker(tickerToken);
  const stopValue = positiveDecimal(stopToken, { price: true, scale: 4 });
  if (!ticker || stopValue === null) return reject();
  return { ok: true, command: { operation: "stop", ticker, stop: stopValue } };
}

function plan(tickerToken, amountToken, cadenceToken, dateToken, bucketToken) {
  const ticker = normalizedTicker(tickerToken);
  const amount = positiveDecimal(amountToken, { price: true, scale: 2, maxWhole: 1000000000 });
  const cadence = cadenceToken?.toLowerCase();
  const nextDueOn = tradeDate(dateToken);
  const bucket = bucketToken?.toLowerCase();
  if (!ticker || amount === null || cadence !== "monthly" || nextDueOn === null ||
      nextDueOn === undefined || bucket !== "core") return reject();
  return {
    ok: true,
    command: {
      operation: "plan", ticker, deposit_amount: amount, cadence,
      next_due_on: nextDueOn, bucket,
    },
  };
}

function cancelPlan(tickerToken) {
  const ticker = normalizedTicker(tickerToken);
  return ticker
    ? { ok: true, command: { operation: "cancel_plan", ticker } }
    : reject();
}

export function parsePortfolioCommand(input) {
  if (typeof input !== "string") return reject();
  const text = input.trim().replace(/\s+/g, " ");
  if (!text) return reject();

  let match = text.match(/^\/start(?:@[A-Za-z0-9_]+)? (\S+)$/i);
  if (match) {
    const code = match[1].toUpperCase();
    return PAIRING_CODE.test(code)
      ? { ok: true, command: { operation: "pair", code } }
      : reject();
  }
  match = text.match(/^\/relink(?:@[A-Za-z0-9_]+)? (\S+)$/i);
  if (match) {
    const code = match[1].toUpperCase();
    return PAIRING_CODE.test(code)
      ? { ok: true, command: { operation: "pair", code, confirm_relink: true } }
      : reject();
  }
  if (/^\/status(?:@[A-Za-z0-9_]+)?$/i.test(text)) return { ok: true, command: { operation: "status" } };
  if (/^\/unlink(?:@[A-Za-z0-9_]+)?$/i.test(text)) return { ok: true, command: { operation: "unlink" } };

  if (/^\/portfolio(?:@[A-Za-z0-9_]+)?$/i.test(text)) return { ok: true, command: { operation: "portfolio" } };
  if (/^\/plans(?:@[A-Za-z0-9_]+)?$/i.test(text)) return { ok: true, command: { operation: "plans" } };
  if (/^\/help(?:@[A-Za-z0-9_]+)?$/i.test(text)) return { ok: true, command: { operation: "help" } };

  match = text.match(/^\/plan(?:@[A-Za-z0-9_]+)? (\S+) (\S+) (\S+) (\S+) (\S+)$/i);
  if (match) return plan(match[1], match[2], match[3], match[4], match[5]);
  match = text.match(/^plan (\S+) (\S+) (\S+) next (\S+) (\S+)$/i);
  if (match) return plan(match[1], match[2], match[3], match[4], match[5]);
  match = text.match(/^\/cancelplan(?:@[A-Za-z0-9_]+)? (\S+)$/i);
  if (match) return cancelPlan(match[1]);
  match = text.match(/^cancel plan (\S+)$/i);
  if (match) return cancelPlan(match[1]);

  match = text.match(/^\/buy(?:@[A-Za-z0-9_]+)? (\S+) (\S+) (\S+)(?: (\S+))? on (\S+)$/i);
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
