import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { LucirelProductBrand } from "@/components/LucirelProductBrand";

describe("LucirelProductBrand", () => {
  it("renders the canonical Wave Gate endorsement", () => {
    render(<LucirelProductBrand />);

    expect(screen.getByRole("img", { name: "Lucirel Wave Gate" })).toBeInTheDocument();
    expect(screen.getByText("NCKUall")).toBeInTheDocument();
    expect(screen.getByText(/by Lucirel/)).toBeInTheDocument();
  });
});
