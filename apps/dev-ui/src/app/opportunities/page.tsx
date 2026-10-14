import type { Metadata } from "next";

import { NotShown, OpportunityBoard } from "@/components/opportunities";
import { PageTitle } from "@/components/ui";
import { loadOpportunityReport, parseView } from "@/private/opportunities/loader";

export const metadata: Metadata = { title: "Creator opportunities (private)" };
// Read on each request from local disk, when enabled; never rendered at build time.
export const dynamic = "force-dynamic";

export default async function OpportunitiesPage({
  searchParams,
}: {
  searchParams: Promise<{ view?: string | string[] }>;
}) {
  const view = parseView((await searchParams).view);
  const loaded = await loadOpportunityReport(view);
  return (
    <>
      <PageTitle
        title="Creator opportunities"
        lede="What the verified evidence says a creator could cover now, and why — or why it cannot say. Private development only."
      />
      {loaded.kind === "ok" ? <OpportunityBoard report={loaded.report} view={view} /> : <NotShown loaded={loaded} />}
    </>
  );
}
