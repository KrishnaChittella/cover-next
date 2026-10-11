import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { FeaturesView } from "@/contract";
import { game } from "@/test/bundle";

import { FeaturePanel, featureRows } from "./feature-panel";

const real = game("minecraft").features!;

function valueOf(key: string): HTMLElement {
  const row = document.querySelector(`[data-feature="${key}"]`);
  if (!row) throw new Error(`no feature row ${key}`);
  return row.querySelector("dd") as HTMLElement;
}

describe("the feature panel", () => {
  it("shows the computed values from the artifact", () => {
    render(<FeaturePanel features={real} />);
    expect(valueOf("latest_views")).toHaveTextContent("6,013");
    expect(valueOf("avg_3d")).toHaveTextContent("7,416");
    expect(valueOf("avg_7d")).toHaveTextContent("7,254");
    expect(valueOf("baseline_median_28d")).toHaveTextContent("5,520");
    expect(valueOf("lift_pct")).toHaveTextContent("+34.3%");
    expect(valueOf("lift_7d_pct")).toHaveTextContent("+31.4%");
    expect(valueOf("velocity_pct")).toHaveTextContent("+6.8%");
    expect(valueOf("robust_z_score")).toHaveTextContent("0.89");
    expect(valueOf("history_percentile")).toHaveTextContent("85.4");
    expect(valueOf("staleness_days")).toHaveTextContent("current");
    expect(valueOf("observed_days")).toHaveTextContent("90 of 90");
    expect(valueOf("missing_days")).toHaveTextContent("0");
    expect(valueOf("consecutive_rising_days")).toHaveTextContent("0");
  });

  it("says which way a change points in words as well as by colour", () => {
    render(<FeaturePanel features={{ ...real, velocity_pct: -12.06 }} />);
    expect(valueOf("lift_pct")).toHaveTextContent("(up)");
    expect(valueOf("velocity_pct")).toHaveTextContent("−12.1%");
    expect(valueOf("velocity_pct")).toHaveTextContent("(down)");
  });

  const NULLABLE = [
    "latest_views",
    "avg_3d",
    "avg_7d",
    "prior_avg_3d",
    "baseline_median_28d",
    "lift_pct",
    "lift_7d_pct",
    "velocity_pct",
    "robust_z_score",
    "history_percentile",
    "staleness_days",
  ] as const;

  it.each(NULLABLE)("shows a null %s as not available, never as zero", (key) => {
    render(<FeaturePanel features={{ ...real, [key]: null } as FeaturesView} />);
    const cell = valueOf(key);
    expect(cell).toHaveTextContent("—");
    expect(cell).toHaveTextContent("not available");
    expect(cell.textContent).not.toMatch(/\d/);
  });

  it("treats a field the artifact leaves out the same as null", () => {
    const partial = { ...real } as Partial<FeaturesView>;
    delete partial.lift_pct;
    delete partial.history_percentile;
    render(<FeaturePanel features={partial as FeaturesView} />);
    expect(valueOf("lift_pct").textContent).not.toMatch(/\d/);
    expect(valueOf("history_percentile").textContent).not.toMatch(/\d/);
  });

  it("keeps a real zero as zero", () => {
    render(<FeaturePanel features={{ ...real, lift_pct: 0, latest_views: 0, robust_z_score: 0 }} />);
    expect(valueOf("lift_pct")).toHaveTextContent("0.0%");
    expect(valueOf("lift_pct")).toHaveTextContent("(unchanged)");
    expect(valueOf("latest_views")).toHaveTextContent("0");
    expect(valueOf("robust_z_score")).toHaveTextContent("0.00");
    expect(valueOf("latest_views")).not.toHaveTextContent("not available");
  });

  it("covers every feature the checkpoint asks for", () => {
    const keys = featureRows(real).flatMap((group) => group.rows.map((row) => row.key));
    for (const key of [...NULLABLE, "consecutive_rising_days", "observed_days", "missing_days"]) {
      expect(keys).toContain(key);
    }
    expect(new Set(keys).size).toBe(keys.length);
  });

  it("explains what a dash means", () => {
    render(<FeaturePanel features={real} />);
    expect(screen.getByText(/A dash means the run could not compute the feature; it does not mean\s+zero/)).toBeInTheDocument();
  });
});
