import { useState } from "react";
import { AlertTriangle, Loader2, RefreshCw, Search } from "lucide-react";
import ReactMarkdown from "react-markdown";
import { api, type StockAnalystResponse } from "@/lib/api";

export function StockAnalyst() {
  const [query, setQuery] = useState("Analyze TCS");
  const [result, setResult] = useState<StockAnalystResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [paperMessage, setPaperMessage] = useState<string | null>(null);

  async function analyze() {
    const trimmed = query.trim();
    if (!trimmed) return;
    setLoading(true);
    setError(null);
    try {
      setResult(await api.analyzeStock({ query: trimmed }));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to analyze this request.");
    } finally {
      setLoading(false);
    }
  }

  async function paperTrade() {
    const symbol = result?.symbols[0];
    if (!symbol) return;
    const quantityText = window.prompt(`Quantity to BUY for ${symbol} (paper account only):`, "1");
    if (quantityText === null) return;
    const quantity = Number(quantityText);
    if (!Number.isFinite(quantity) || quantity <= 0) {
      setPaperMessage("Enter a positive quantity to place an explicit paper trade.");
      return;
    }
    try {
      await api.paperBuy({ symbol, quantity, reason: "Stock Analyst explicit paper trade", rationale: result?.analysis.slice(0, 1000) });
      setPaperMessage(`Paper BUY submitted for ${quantity} ${symbol}.`);
    } catch (err) {
      setPaperMessage(err instanceof Error ? err.message : "Paper trade failed.");
    }
  }

  return (
    <main className="mx-auto w-full max-w-6xl space-y-5 p-4 md:p-8">
      <header>
        <p className="text-xs font-medium uppercase tracking-[0.18em] text-primary">Research workspace</p>
        <h1 className="mt-1 text-2xl font-semibold tracking-tight">Stock Analyst</h1>
        <p className="mt-2 max-w-3xl text-sm text-muted-foreground">
          Ask for evidence-based research on Indian or global equities. The local Ollama model explains the available data;
          it does not guarantee returns or place trades.
        </p>
      </header>

      <section className="rounded-xl border border-border/70 bg-card p-4 shadow-sm">
        <label htmlFor="stock-analysis-query" className="text-sm font-medium">What would you like to research?</label>
        <div className="mt-2 flex flex-col gap-2 sm:flex-row">
          <input
            id="stock-analysis-query"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => { if (event.key === "Enter" && !loading) void analyze(); }}
            placeholder="Analyze TCS, compare AAPL and MSFT, or ask about risks"
            className="min-h-10 flex-1 rounded-md border border-border bg-background px-3 text-sm outline-none ring-primary/30 focus:ring-2"
          />
          <button
            type="button"
            onClick={() => void analyze()}
            disabled={loading || !query.trim()}
            className="inline-flex min-h-10 items-center justify-center gap-2 rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground disabled:opacity-50"
          >
            {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Search className="h-4 w-4" />}
            {loading ? "Researching…" : "Analyze"}
          </button>
          {result && (
            <button type="button" onClick={() => void analyze()} disabled={loading} className="inline-flex min-h-10 items-center justify-center gap-2 rounded-md border border-border px-3 text-sm hover:bg-muted">
              <RefreshCw className="h-4 w-4" /> Refresh
            </button>
          )}
        </div>
      </section>

      {error && (
        <div role="alert" className="flex items-start gap-2 rounded-lg border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" /> <span>{error}</span>
        </div>
      )}

      {result && (
        <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_280px]">
          <article className="rounded-xl border border-border/70 bg-card p-5 shadow-sm">
            <div className="prose prose-sm max-w-none dark:prose-invert">
              <ReactMarkdown>{result.analysis}</ReactMarkdown>
            </div>
            <div className="mt-6 flex flex-wrap items-center gap-3 border-t border-border/60 pt-4">
              <button type="button" onClick={() => void paperTrade()} className="rounded-md border border-primary/40 px-3 py-2 text-sm font-medium text-primary hover:bg-primary/10">
                Paper Trade {result.symbols[0]}
              </button>
              <span className="text-xs text-muted-foreground">Only runs after you confirm a quantity; never automatic.</span>
              {paperMessage && <span className="w-full text-xs text-muted-foreground">{paperMessage}</span>}
            </div>
            <p className="mt-6 border-t border-border/60 pt-3 text-xs text-muted-foreground">{result.disclaimer}</p>
          </article>
          <aside className="space-y-4">
            {result.evidence.map((item) => (
              <section key={item.symbol} className="rounded-xl border border-border/70 bg-card p-4">
                <h2 className="font-semibold">{item.symbol}</h2>
                {item.market_data ? (
                  <dl className="mt-3 space-y-2 text-sm">
                    <div className="flex justify-between gap-3"><dt className="text-muted-foreground">Recent price</dt><dd>{item.market_data.price}</dd></div>
                    <div className="flex justify-between gap-3"><dt className="text-muted-foreground">Trend</dt><dd>{item.market_data.trend || "—"}</dd></div>
                    <div className="flex justify-between gap-3"><dt className="text-muted-foreground">Observed</dt><dd>{item.observed_at ? new Date(item.observed_at).toLocaleString() : "—"}</dd></div>
                  </dl>
                ) : <p className="mt-2 text-sm text-muted-foreground">Market data unavailable.</p>}
                {!!item.missing?.length && <p className="mt-3 text-xs text-muted-foreground">Unavailable: {item.missing.join(", ")}</p>}
              </section>
            ))}
            {!!result.paper_positions.length && (
              <section className="rounded-xl border border-primary/30 bg-primary/5 p-4 text-sm">
                <h2 className="font-semibold">Paper portfolio context</h2>
                <p className="mt-2 text-muted-foreground">Existing paper positions were supplied to the analyst; no trade was placed.</p>
              </section>
            )}
          </aside>
        </div>
      )}
    </main>
  );
}
