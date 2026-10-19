import type { Metadata } from "next";

import { NotShown, OpportunityDetail } from "@/components/opportunities";
import { loadOpportunityReport, parseView } from "@/private/opportunities/loader";

export const metadata: Metadata = { title: "Creator opportunity (private)" };
export const dynamic = "force-dynamic";

export default async function OpportunityPage({
  params,
  searchParams,
}: {
  params: Promise<{ slug: string }>;
  searchParams: Promise<{ view?: string | string[] }>;
}) {
  const { slug } = await params;
  const view = parseView((await searchParams).view);
  const loaded = await loadOpportunityReport(view);
  return loaded.kind === "ok" ? (
    <OpportunityDetail report={loaded.report} slug={slug} view={view} />
  ) : (
    <NotShown loaded={loaded} />
  );
}
