import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ApiError } from "../api";
import { Chip, ErrorNote } from "../components/ui";

describe("ui", () => {
  it("shows an API error with what to do next and the fields to fix", () => {
    render(<ErrorNote error={new ApiError(422, "Fix", "Some details need correcting", "Check the form.", [{ field: "body.title", message: "too short" }])} />);
    expect(screen.getByRole("alert").textContent).toContain("Some details need correcting Check the form.");
    expect(screen.getByText("Title: too short")).toBeTruthy();
  });

  it("marks classification in text, not colour alone", () => {
    render(<Chip level="restricted" />);
    expect(screen.getByText("restricted").className).toContain("cls-restricted");
  });
});
