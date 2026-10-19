import type { Metadata } from "next";

import { Methodology } from "@/components/methodology";
import { PageTitle } from "@/components/ui";

export const metadata: Metadata = { title: "Methodology" };

export default function MethodologyPage() {
  return (
    <>
      <PageTitle title="Methodology" lede="How the current score and its confidence are computed, and what they leave out." />
      <Methodology />
    </>
  );
}
