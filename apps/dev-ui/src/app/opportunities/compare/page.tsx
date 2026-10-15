import type { Metadata } from "next";
import Link from "next/link";

import { NotShown, OpportunityComparison } from "@/components/opportunities";
import { PageTitle } from "@/components/ui";
import { buildComparison, parseSelection } from "@/private/opportunities/compare";
import { loadOpportunityReport, parseView } from "@/private/opportunities/loader";

export const metadata: Metadata = { title: "Compare creator opportunities (private)" };
// Read on each request from local disk, when enabled; never rendered at build time.
export const dynamic = "force-dynamic";

export default async function ComparePage({
  searchParams,
}: {
  searchParams: Promise<{ view?: string | string[]; game?: string | string[] }>;
}) {
  const params = await searchParams;
  const view = parseView(params.view);
  const loaded = await loadOpportunityReport(view);
  if (loaded.kind !== "ok") return <NotShown loaded={loaded} />;
  const selection = parseSelection(params.game, loaded.report);
  return (
    <>
      <PageTitle
        title="Compare creator opportunities"
        lede="Two or three games from the same private report, side by side. The engine's order and readiness, unchanged; missing evidence shown as unknown."
      />
      {selection.kind === "ok" ? (
        <OpportunityComparison comparison={buildComparison(loaded.report, selection.slugs, view)} view={view} />
      ) : (
        <p role="alert" className="panel border-warn/60 p-4 text-sm text-ink" data-testid="selection-invalid">
          {selection.message}{" "}
          <Link href={view === "synthetic" ? "/opportunities?view=synthetic#compare" : "/opportunities#compare"} className="underline">
            Choose again
          </Link>
        </p>
      )}
    </>
  );
}
