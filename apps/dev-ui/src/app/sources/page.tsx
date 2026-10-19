import type { Metadata } from "next";

import { SourceHealth } from "@/components/source-health";
import { PageTitle } from "@/components/ui";

export const metadata: Metadata = { title: "Sources" };

export default function SourcesPage() {
  return (
    <>
      <PageTitle
        title="Sources"
        lede="What each source returned in the run this interface is showing. This is the artifact's own account of that one run, not a live view of the collection system."
      />
      <SourceHealth />
    </>
  );
}
