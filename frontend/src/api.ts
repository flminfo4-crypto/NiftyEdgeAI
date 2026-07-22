export interface Status {
  connected: boolean;
  detail: string;
  instruments_loaded: boolean;
}
export interface CoreInstrument {
  symbol: string;
  security_id: string;
  exchange: string;
}

export async function fetchStatus(): Promise<Status> {
  const r = await fetch("/api/status");
  return r.json();
}

export async function fetchCore(): Promise<Record<string, CoreInstrument>> {
  const r = await fetch("/api/instruments/core");
  return r.json();
}
