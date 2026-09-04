import { describe, expect, it } from "vitest";

import { priceReceiptContext } from "./price-context";

describe("price receipt context", () => {
  it("fails closed to an unavailable timestamp when receipt time is malformed", () => {
    expect(priceReceiptContext("as_of_close", "not-a-date", ["yahoo-chart"]))
      .toBe("as of close · time unavailable ET · Yahoo Finance");
  });
});
