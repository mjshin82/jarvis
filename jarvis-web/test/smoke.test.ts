import { describe, it, expect } from "vitest";

describe("test runner", () => {
  it("runs in a happy-dom environment", () => {
    expect(typeof document).toBe("object");
    const el = document.createElement("div");
    el.textContent = "hi";
    expect(el.tagName).toBe("DIV");
    expect(el.textContent).toBe("hi");
  });
});
