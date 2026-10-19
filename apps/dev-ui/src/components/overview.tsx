"use client";

import { loadOverview } from "@/lib/data/client";
import { useArtifact } from "@/lib/data/use-artifact";

import { CapabilityPanel } from "./capability-panel";
import { DataState } from "./data-state";
import { Leaderboard } from "./leaderboard";
import { RunNote } from "./run-note";
import { ScopeNotice } from "./scope-notice";
import { StatusStrip } from "./status-strip";
import { Section } from "./ui";
import { UnrankedList } from "./unranked-list";

export function Overview() {
  const state = useArtifact("overview", loadOverview);
  return (
    <DataState state={state} label="the current leaderboard" rows={10}>
      {({ manifest, data: { current, sourceHealth } }) => (
        <>
          <ScopeNotice scopeNote={current.scope_note} scoreVersion={current.score_version} />

          <Section id="snapshot" title="Snapshot">
            <StatusStrip current={current} sourceHealth={sourceHealth} />
          </Section>

          <Section
            id="leaderboard"
            title="Attention leaderboard"
            note="Ranked by score v0: attention anomaly and recent momentum. The order is the artifact's; this page sorts and filters it but computes nothing."
          >
            <Leaderboard entries={current.entries} />
          </Section>

          <Section
            id="unranked"
            title="Unranked games"
            note="In the catalogue but without a score in this run. Each row gives the reason the run recorded."
          >
            <UnrankedList entries={current.unranked} />
          </Section>

          <Section
            id="capabilities"
            title="What exists today"
            note="The intelligence the current artifacts carry, and what they do not carry yet."
          >
            <CapabilityPanel />
          </Section>

          <RunNote manifest={manifest} />
        </>
      )}
    </DataState>
  );
}
