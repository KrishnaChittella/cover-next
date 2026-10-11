import { Overview } from "@/components/overview";

export default function OverviewPage() {
  return (
    <>
      <header className="mb-6">
        <h1 className="text-3xl font-semibold tracking-tight text-ink">MetaSpike</h1>
        <p className="mt-2 max-w-3xl text-lg text-ink-soft">
          What game has the strongest content opportunity right now, and why?
        </p>
      </header>
      <Overview />
    </>
  );
}
