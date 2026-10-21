import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api, ApiError } from "./api/client";
import { backoffDelay, useLiveSocket } from "./api/live";
import { ConfirmDialog, ErrorAlert, Segmented, Stat } from "./components/ui";
import { fmtBytes, fmtMs, fmtPct } from "./lib/format";

class FakeSocket {
  static instances: FakeSocket[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((e: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;
  constructor(public url: string) { FakeSocket.instances.push(this); }
  close() { if (!this.closed) { this.closed = true; this.onclose?.(); } }
}

describe("WebSocket live metrics", () => {
  beforeEach(() => { FakeSocket.instances = []; vi.useFakeTimers(); });
  afterEach(() => vi.useRealTimers());

  it("backs off exponentially with a ceiling", () => {
    expect([0, 1, 2, 3, 4, 5, 9].map((a) => backoffDelay(a))).toEqual([500, 1000, 2000, 4000, 8000, 10000, 10000]);
  });

  it("applies snapshots, appends points and reconnects after a drop", () => {
    const factory = (u: string) => new FakeSocket(u) as unknown as WebSocket;
    const { result, unmount } = renderHook(() => useLiveSocket(factory));
    expect(result.current.state).toBe("connecting");
    const s1 = FakeSocket.instances[0];
    expect(s1.url).toMatch(/\/ws\/metrics$/);
    act(() => s1.onopen?.());
    expect(result.current.state).toBe("open");
    const snap = { summary: { total_requests: 5 }, servers: [], experiment: null };
    act(() => s1.onmessage?.({ data: JSON.stringify({ type: "snapshot", data: snap, point: { ts: 10, requests: 2 } }) }));
    act(() => s1.onmessage?.({ data: JSON.stringify({ type: "snapshot", data: snap, point: { ts: 10, requests: 2 } }) }));
    act(() => s1.onmessage?.({ data: JSON.stringify({ type: "event", event: { kind: "failure_injected", ts: 1 } }) }));
    act(() => s1.onmessage?.({ data: "not json" }));
    expect(result.current.snapshot?.summary.total_requests).toBe(5);
    expect(result.current.points).toHaveLength(1); // duplicate timestamp ignored
    expect(result.current.events[0].kind).toBe("failure_injected");

    act(() => s1.close());
    expect(result.current.state).toBe("closed");
    expect(result.current.retryInMs).toBe(500);
    expect(FakeSocket.instances).toHaveLength(1);
    act(() => { vi.advanceTimersByTime(500); });
    expect(FakeSocket.instances).toHaveLength(2);
    act(() => FakeSocket.instances[1].onopen?.());
    expect(result.current.state).toBe("open");
    expect(result.current.snapshot?.summary.total_requests).toBe(5); // last data kept across reconnect
    unmount();
    act(() => { vi.advanceTimersByTime(20000); });
    expect(FakeSocket.instances).toHaveLength(2); // no reconnect after unmount
  });
});

describe("API client", () => {
  afterEach(() => vi.restoreAllMocks());

  it("surfaces backend error details", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ error: "conflict", detail: "experiment abc is already running" }), { status: 409 }));
    await expect(api.startExperiment("abc")).rejects.toMatchObject({ status: 409, code: "conflict", message: "experiment abc is already running" });
  });

  it("reports an unreachable control plane", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("Failed to fetch"));
    const err = await api.health().catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(0);
  });

  it("sends JSON bodies to the right endpoint", async () => {
    const spy = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ strategy: "hybrid" }), { status: 200 }));
    await api.setRouting("hybrid", { latency_weight: 1, load_weight: 0, packet_loss_weight: 0 });
    const [url, init] = spy.mock.calls[0];
    expect(url).toBe("/api/routing/strategy");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual({ strategy: "hybrid", weights: { latency_weight: 1, load_weight: 0, packet_loss_weight: 0 } });
  });
});

describe("UI states and controls", () => {
  it("shows a skeleton while loading and the value afterwards", () => {
    const { rerender, container } = render(<Stat label="P95" value="12.0 ms" loading />);
    expect(container.querySelector(".skeleton")).toBeInTheDocument();
    rerender(<Stat label="P95" value="12.0 ms" />);
    expect(screen.getByText("12.0 ms")).toBeInTheDocument();
  });

  it("error alert offers retry", () => {
    const retry = vi.fn();
    render(<ErrorAlert message="Control plane unreachable" onRetry={retry} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Control plane unreachable");
    fireEvent.click(screen.getByText("Retry"));
    expect(retry).toHaveBeenCalledOnce();
  });

  it("confirm dialog confirms, cancels and closes on Escape", async () => {
    const ok = vi.fn();
    const cancel = vi.fn();
    render(<ConfirmDialog open title="Simulate failure?" body="x" confirmLabel="Simulate failure" danger onConfirm={ok} onCancel={cancel} />);
    fireEvent.click(screen.getByText("Simulate failure"));
    expect(ok).toHaveBeenCalledOnce();
    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() => expect(cancel).toHaveBeenCalled());
  });

  it("segmented control reports selection and pressed state", () => {
    const onChange = vi.fn();
    render(<Segmented label="Dist" value="zipf" onChange={onChange} options={[{ value: "zipf", label: "Zipf" }, { value: "uniform", label: "Uniform" }]} />);
    expect(screen.getByText("Zipf")).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(screen.getByText("Uniform"));
    expect(onChange).toHaveBeenCalledWith("uniform");
  });

  it("formats missing values as unavailable, never zero", () => {
    expect(fmtMs(null)).toBe("—");
    expect(fmtPct(undefined)).toBe("—");
    expect(fmtMs(1530)).toBe("1.53 s");
    expect(fmtBytes(4 * 1024 * 1024)).toBe("4.0 MB");
  });
});
