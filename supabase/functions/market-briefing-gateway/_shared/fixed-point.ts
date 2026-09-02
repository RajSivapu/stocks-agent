const DECIMAL_PATTERN = /^(?:0|[1-9]\d*)(?:\.\d+)?$/;
const MAX_WHOLE = 1_000_000_000_000_000n;

function scaleFactor(scale: number): bigint {
  if (!Number.isInteger(scale) || scale < 0 || scale > 18) {
    throw new Error("scale must be an integer between 0 and 18");
  }
  return 10n ** BigInt(scale);
}

export function parseFixed(value: string, scale: number): bigint {
  const factor = scaleFactor(scale);
  if (typeof value !== "string" || !DECIMAL_PATTERN.test(value)) {
    throw new Error("value must be a canonical decimal string");
  }

  const [wholeText, fractionText = ""] = value.split(".");
  if (fractionText.length > scale) {
    throw new Error(`value exceeds ${scale} fractional digits`);
  }

  const whole = BigInt(wholeText);
  if (whole > MAX_WHOLE) {
    throw new Error("value exceeds maximum whole-unit magnitude");
  }

  const fraction = fractionText
    ? BigInt(fractionText.padEnd(scale, "0"))
    : 0n;
  return whole * factor + fraction;
}

export function multiplyFixed(
  left: bigint,
  right: bigint,
  rightScale: number,
): bigint {
  return (left * right) / scaleFactor(rightScale);
}

export function formatFixed(value: bigint, scale: number): string {
  const factor = scaleFactor(scale);
  const negative = value < 0n;
  const magnitude = negative ? -value : value;
  const whole = magnitude / factor;
  if (scale === 0) return `${negative ? "-" : ""}${whole}`;

  const fraction = (magnitude % factor).toString().padStart(scale, "0")
    .replace(/0+$/, "");
  return `${negative ? "-" : ""}${whole}${fraction ? `.${fraction}` : ""}`;
}

