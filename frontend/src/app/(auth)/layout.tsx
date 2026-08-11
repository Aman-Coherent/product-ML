import { Boxes } from "lucide-react";

export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="grid min-h-screen lg:grid-cols-2">
      <div className="flex flex-col justify-center px-6 py-12 sm:px-12 lg:px-20">
        <div className="mx-auto w-full max-w-sm">
          <div className="mb-8 flex items-center gap-2">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-primary text-primary-foreground">
              <Boxes className="h-5 w-5" />
            </div>
            <span className="text-lg font-semibold">ProductGen AI</span>
          </div>
          {children}
        </div>
      </div>
      <div className="hidden bg-muted/40 lg:flex lg:flex-col lg:justify-center lg:px-16 relative overflow-hidden border-l">
        <div className="absolute inset-0 bg-[radial-gradient(circle_at_top_left,theme(colors.primary/15%),transparent_50%)]" />
        <div className="relative z-10 max-w-lg">
          <h2 className="text-3xl font-bold tracking-tight">
            Classify companies. Generate catalogs. At 200,000-company scale.
          </h2>
          <p className="mt-4 text-muted-foreground">
            Give us a company name, location, and website — our pipeline reads the
            actual website content with an LLM, classifies the supply chain role, and
            generates a realistic 10-50 product catalog. Live progress, pause/resume,
            and full export included.
          </p>
          <div className="mt-10 grid grid-cols-2 gap-4">
            {[
              ["Website-aware", "LLM reads the real company page, not guesses"],
              ["Multi-category", "Packaging + Machinery + more, detected automatically"],
              ["Crash-safe", "Checkpointed jobs resume exactly where they stopped"],
              ["Scales to 200K", "Cursor-paginated virtual table, streamed exports"],
            ].map(([title, desc]) => (
              <div key={title} className="rounded-lg border bg-background/60 p-4 backdrop-blur">
                <p className="text-sm font-medium">{title}</p>
                <p className="mt-1 text-xs text-muted-foreground">{desc}</p>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
