import type { Metadata } from "next";

import { Radar } from "@/components/radar";
import { PageTitle } from "@/components/ui";

export const metadata: Metadata = { title: "Radar" };

export default function RadarPage() {
  return (
    <>
      <PageTitle
        title="Upcoming radar"
        lede="Dated events that have not started yet, soonest first, for games whose events a source has looked at."
      />
      <Radar />
    </>
  );
}
