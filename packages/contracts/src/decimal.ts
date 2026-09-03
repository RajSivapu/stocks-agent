export type Money = string;
export type Shares = string;
export type Price = string;

interface DecimalRules {
  field: string;
  scale: number;
  allowZero: boolean;
  maxWhole: bigint;
}

function parseDecimal(value: unknown, rules: DecimalRules): string {
  if (typeof value !== "string" || value.length === 0 || value.trim() !== value) {
    throw new Error(`${rules.field} must be a decimal string`);
  }
  const match = /^(0|[0-9]+)(?:\.([0-9]+))?$/.exec(value);
  if (!match) throw new Error(`${rules.field} must use plain unsigned decimal notation`);
  const fraction = match[2] ?? "";
  if (fraction.length > rules.scale) {
    throw new Error(`${rules.field} supports at most ${rules.scale} decimals`);
  }
  const whole = BigInt(match[1]);
  const nonzeroFraction = /[1-9]/.test(fraction);
  if (!rules.allowZero && whole === 0n && !nonzeroFraction) {
    throw new Error(`${rules.field} must be positive`);
  }
  if (whole > rules.maxWhole || (whole === rules.maxWhole && nonzeroFraction)) {
    throw new Error(`${rules.field} exceeds maximum`);
  }
  const canonicalWhole = whole.toString();
  const canonicalFraction = fraction.replace(/0+$/, "");
  return canonicalFraction ? `${canonicalWhole}.${canonicalFraction}` : canonicalWhole;
}

export function parseShares(value: unknown): Shares {
  return parseDecimal(value, {
    field: "shares",
    scale: 8,
    allowZero: false,
    maxWhole: 1_000_000n,
  });
}

export function parsePrice(value: unknown): Price {
  return parseDecimal(value, {
    field: "price",
    scale: 4,
    allowZero: false,
    maxWhole: 1_000_000n,
  });
}

export function parseMoney(value: unknown): Money {
  return parseDecimal(value, {
    field: "money",
    scale: 2,
    allowZero: true,
    maxWhole: 1_000_000_000_000n,
  });
}
