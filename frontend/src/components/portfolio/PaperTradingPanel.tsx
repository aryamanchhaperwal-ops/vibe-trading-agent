import { useEffect, useState } from "react";
import { api, type PaperMonitorStatus, type PaperPortfolio } from "@/lib/api";

export function PaperTradingPanel() {
  const [portfolio, setPortfolio] = useState<PaperPortfolio | null>(null);
  const [symbol, setSymbol] = useState("TCS");
  const [quantity, setQuantity] = useState("10");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [monitor, setMonitor] = useState<PaperMonitorStatus | null>(null);
  const [scanning, setScanning] = useState(false);
  const [monitorBusy, setMonitorBusy] = useState(false);

  async function load() {
    const result = await api.getPaperPortfolio();
    setPortfolio(result.portfolio);
    const monitorResult = await api.getPaperMonitorStatus();
    setMonitor(monitorResult.monitor);
  }

  useEffect(() => {
    void load().catch(() => setMessage("Paper portfolio is unavailable."));
  }, []);

  async function trade(side: "buy" | "sell") {
    setBusy(true);
    setMessage(null);
    try {
      const result = side === "buy"
        ? await api.paperBuy({ symbol, quantity: Number(quantity) })
        : await api.paperSell({ symbol, quantity: Number(quantity) });
      setPortfolio(result.trade.portfolio);
      setMessage(`${side.toUpperCase()} completed in paper trading.`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Paper trade failed.");
    } finally {
      setBusy(false);
    }

  }

  async function scan() {
    setScanning(true);
    try {
      const result = await api.scanNifty500();
      setMessage(`Scanned ${result.scanned} stocks in ${result.batches} batches.`);
      const status = await api.getPaperMonitorStatus();
      setMonitor(status.monitor);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "NIFTY 500 scan failed.");
    } finally {
      setScanning(false);
    }

  }

  async function toggleMonitoring() {
    if (!monitor) return;
    setMonitorBusy(true);
    try {
      const result = await api.configurePaperMonitor({
        enabled: !monitor.enabled,
        cadence_seconds: monitor.cadence_seconds,
        movement_threshold: monitor.movement_threshold,
      });
      setMonitor(result.config);
      setMessage(`Paper monitoring ${result.config.enabled ? "enabled" : "disabled"}.`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Unable to update monitoring.");
    } finally {
      setMonitorBusy(false);
    }
  }

  return (
    <section className="rounded-xl border bg-card p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="font-semibold">Paper Trading</h2>
          <p className="text-xs text-muted-foreground">Local virtual account; no broker orders are placed.</p>
        </div>
        <button onClick={() => void api.refreshPaperPortfolio().then((r) => setPortfolio(r.portfolio))} className="rounded-md border px-3 py-1.5 text-xs">
          Refresh prices
        </button>
        <button disabled={scanning} onClick={() => void scan()} className="rounded-md bg-primary px-3 py-1.5 text-xs text-primary-foreground disabled:opacity-50">
          {scanning ? "Scanning…" : "Scan NIFTY 500"}
        </button>
      </div>
      {portfolio ? (
        <div className="mt-4 grid gap-3 sm:grid-cols-4">
          <Metric label="Total value" value={`₹${portfolio.total_value.toFixed(2)}`} />
          <Metric label="Cash" value={`₹${portfolio.cash.toFixed(2)}`} />
          <Metric label="Unrealized P&L" value={`₹${portfolio.unrealized_pnl.toFixed(2)}`} />
          <Metric label="Return" value={`${(portfolio.total_return * 100).toFixed(2)}%`} />
        </div>
      ) : null}
      <div className="mt-4 flex flex-wrap gap-2">
        <input value={symbol} onChange={(event) => setSymbol(event.target.value)} aria-label="Paper symbol" className="w-28 rounded-md border bg-background px-3 py-2 text-sm" />
        <input value={quantity} onChange={(event) => setQuantity(event.target.value)} aria-label="Paper quantity" type="number" min="1" className="w-24 rounded-md border bg-background px-3 py-2 text-sm" />
        <button disabled={busy} onClick={() => void trade("buy")} className="rounded-md bg-primary px-3 py-2 text-sm text-primary-foreground disabled:opacity-50">Buy</button>
        <button disabled={busy} onClick={() => void trade("sell")} className="rounded-md border px-3 py-2 text-sm disabled:opacity-50">Sell</button>
      </div>
      {message ? <p className="mt-3 text-xs text-muted-foreground">{message}</p> : null}
      {monitor ? (
        <div className="mt-4 rounded-lg border p-3 text-xs">
          <div className="flex flex-wrap justify-between gap-2">
            <span>Monitoring: <strong>{monitor.enabled ? "enabled" : "disabled"}</strong></span>
            <span>Scanned: {monitor.scanned}</span>
            <span>Last scan: {monitor.last_scan ? new Date(monitor.last_scan).toLocaleString() : "—"}</span>
            <button disabled={monitorBusy} onClick={() => void toggleMonitoring()} className="rounded border px-2 py-1 disabled:opacity-50">
              {monitor.enabled ? "Disable" : "Enable"}
            </button>
          </div>
          {monitor.last_error ? <p className="mt-2 text-danger">{monitor.last_error}</p> : null}
          {monitor.latest_opportunities.length ? <div className="mt-2"><strong>Latest opportunities:</strong> {monitor.latest_opportunities.slice(0, 5).map((item) => `${item.symbol} ${item.decision}`).join(", ")}</div> : null}
          {monitor.latest_alerts.length ? <div className="mt-2 space-y-1">{monitor.latest_alerts.slice(0, 3).map((alert) => <p key={`${alert.timestamp}-${alert.symbol}`}>{alert.symbol}: {alert.message}</p>)}</div> : null}
        </div>
      ) : null}
      {portfolio?.positions.length ? (
        <div className="mt-4 overflow-x-auto"><table className="w-full text-left text-sm"><thead><tr className="border-b text-xs text-muted-foreground"><th className="py-2">Symbol</th><th>Qty</th><th>Price</th><th>P&L</th></tr></thead><tbody>{portfolio.positions.map((position) => <tr key={position.symbol} className="border-b last:border-0"><td className="py-2 font-medium">{position.symbol}</td><td>{position.quantity}</td><td>₹{position.current_price.toFixed(2)}</td><td className={position.unrealized_pnl >= 0 ? "text-positive" : "text-danger"}>₹{position.unrealized_pnl.toFixed(2)}</td></tr>)}</tbody></table></div>
      ) : null}
    </section>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return <div><div className="text-xs text-muted-foreground">{label}</div><div className="mt-1 font-semibold">{value}</div></div>;
}
