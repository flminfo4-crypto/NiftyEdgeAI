import { useEffect, useState, type CSSProperties } from "react";
import { fetchStatus, fetchCore, Status, CoreInstrument } from "./api";

export default function App() {
  const [status, setStatus] = useState<Status | null>(null);
  const [core, setCore] = useState<Record<string, CoreInstrument>>({});
  const [lastBeat, setLastBeat] = useState<string>("—");

  useEffect(() => {
    const load = () => {
      fetchStatus().then(setStatus).catch(() =>
        setStatus({ connected: false, detail: "Backend not reachable — is it running?", instruments_loaded: false }));
      fetchCore().then(setCore).catch(() => {});
    };
    load();
    const poll = setInterval(load, 15000);

    const ws = new WebSocket(`ws://${location.host}/ws`);
    ws.onmessage = (e) => {
      const m = JSON.parse(e.data);
      if (m.type === "heartbeat") setLastBeat(m.ts);
    };
    return () => { clearInterval(poll); ws.close(); };
  }, []);

  const dot = (ok: boolean) => (
    <span style={{
      display: "inline-block", width: 10, height: 10, borderRadius: 5,
      background: ok ? "var(--green)" : "var(--red)", marginRight: 8,
    }} />
  );

  return (
    <div style={{ padding: 24, maxWidth: 900, margin: "0 auto" }}>
      <h1 style={{ color: "var(--accent)", marginBottom: 4 }}>NiftyEdge</h1>
      <div style={{ color: "var(--muted)", marginBottom: 24 }}>Pro Trading Terminal — Story 0</div>

      <div style={panel}>
        <h3>Connection</h3>
        <p>{dot(!!status?.connected)}
          Dhan API: {status ? (status.connected ? "Connected" : "Not connected") : "checking…"}</p>
        {status && !status.connected && (
          <p style={{ color: "var(--red)", marginTop: 8 }}>{status.detail}</p>
        )}
        <p style={{ marginTop: 8 }}>{dot(lastBeat !== "—")}
          Backend heartbeat: {lastBeat}</p>
      </div>

      <div style={panel}>
        <h3>Core Instruments</h3>
        {Object.keys(core).length === 0 ? (
          <p style={{ color: "var(--muted)" }}>Not loaded yet…</p>
        ) : (
          <table style={{ width: "100%", borderCollapse: "collapse", marginTop: 8 }}>
            <thead>
              <tr style={{ color: "var(--muted)", textAlign: "left" }}>
                <th>Symbol</th><th>Security ID</th><th>Exchange</th>
              </tr>
            </thead>
            <tbody>
              {Object.values(core).map((i) => (
                <tr key={i.symbol} style={{ borderTop: "1px solid var(--border)" }}>
                  <td style={{ padding: "6px 0" }}>{i.symbol}</td>
                  <td>{i.security_id}</td>
                  <td>{i.exchange}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

const panel: CSSProperties = {
  background: "var(--panel)",
  border: "1px solid var(--border)",
  borderRadius: 10,
  padding: 16,
  marginBottom: 16,
};
