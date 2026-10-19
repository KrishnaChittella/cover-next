import Link from "next/link";

export default function NotFound() {
  return (
    <div className="panel p-6">
      <h1 className="text-xl font-semibold text-ink">Page not found</h1>
      <p className="mt-2 text-sm text-ink-soft">There is nothing at this address.</p>
      <p className="mt-4 text-sm">
        <Link href="/" className="text-ink underline decoration-line-strong underline-offset-4 hover:decoration-signal">
          Back to the overview
        </Link>
      </p>
    </div>
  );
}
