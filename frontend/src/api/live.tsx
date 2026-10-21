import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import type { CdnEvent, Snapshot, TimePoint } from "./types";

export type ConnState = "connecting" | "open" | "closed";

export interface LiveState {
  state: ConnState;
  snapshot: Snapshot | null;
  points: TimePoint[];
  events: CdnEvent[];
  lastMessageAt: number | null;
  retryInMs: number | null;
}

/** Exponential backoff with a ceiling: 0.5s, 1s, 2s, 4s, 8s, 10s, 10s ... */
export function backoffDelay(attempt: number, baseMs = 500, maxMs = 10_000): number {
  return Math.min(maxMs, baseMs * 2 ** Math.max(0, attempt));
}

export function wsUrl(path = "/ws/metrics"): string {
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${window.location.host}${path}`;
}

type SocketFactory = (url: string) => WebSocket;

/**
 * Maintains one WebSocket to /ws/metrics, reconnecting with backoff. Snapshots replace state;
 * per-second points are appended to a bounded series; events are prepended to a bounded log.
 */
export function useLiveSocket(factory: SocketFactory = (u) => new WebSocket(u), maxPoints = 300): LiveState {
  const [live, setLive] = useState<LiveState>({
    state: "connecting", snapshot: null, points: [], events: [], lastMessageAt: null, retryInMs: null,
  });
  const attempt = useRef(0);

  useEffect(() => {
    let ws: WebSocket | null = null;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let disposed = false;

    const connect = () => {
      if (disposed) return;
      setLive((s) => ({ ...s, state: "connecting", retryInMs: null }));
      ws = factory(wsUrl());
      ws.onopen = () => {
        attempt.current = 0;
        setLive((s) => ({ ...s, state: "open" }));
      };
      ws.onmessage = (ev) => {
        let msg: { type: string; data?: Snapshot; point?: TimePoint | null; event?: CdnEvent };
        try {
          msg = JSON.parse(ev.data);
        } catch {
          return;
        }
        setLive((s) => {
          const next = { ...s, lastMessageAt: Date.now() };
          if (msg.type === "snapshot" && msg.data) {
            next.snapshot = msg.data;
            const p = msg.point;
            if (p && (!s.points.length || s.points[s.points.length - 1].ts < p.ts)) {
              next.points = [...s.points, p].slice(-maxPoints);
            }
          } else if (msg.type === "event" && msg.event) {
            next.events = [msg.event, ...s.events].slice(0, 100);
          }
          return next;
        });
      };
      ws.onclose = () => {
        if (disposed) return;
        const delay = backoffDelay(attempt.current++);
        setLive((s) => ({ ...s, state: "closed", retryInMs: delay }));
        timer = setTimeout(connect, delay);
      };
      ws.onerror = () => ws?.close();
    };

    connect();
    return () => {
      disposed = true;
      clearTimeout(timer);
      ws?.close();
    };
  }, [factory, maxPoints]);

  return live;
}

const LiveContext = createContext<LiveState | null>(null);

const defaultFactory: SocketFactory = (u) => new WebSocket(u);

export function LiveProvider({ children }: { children: ReactNode }) {
  const live = useLiveSocket(defaultFactory);
  return <LiveContext.Provider value={live}>{children}</LiveContext.Provider>;
}

export function useLive(): LiveState {
  const ctx = useContext(LiveContext);
  if (!ctx) throw new Error("useLive must be used inside <LiveProvider>");
  return ctx;
}
