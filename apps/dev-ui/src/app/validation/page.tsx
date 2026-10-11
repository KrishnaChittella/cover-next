import type { Metadata } from "next";

import { Validation } from "@/components/validation";
import { PageTitle } from "@/components/ui";

export const metadata: Metadata = { title: "Validation" };

export default function ValidationPage() {
  return (
    <>
      <PageTitle
        title="Historical validation"
        lede="When score v0 ranked a game highly on a past day, what did that game's attention do next? Computed with no look-ahead, and reported as it came out."
      />
      <Validation />
    </>
  );
}
