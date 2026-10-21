import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { AlertCircle, CheckCircle2, Info, Inbox, Loader2, X } from "lucide-react";
import type { HealthStatus } from "../api/types";

export function Panel({ title, subtitle, actions, children, footer, flush, className = "" }: {
  title?: ReactNode; subtitle?: ReactNode; actions?: ReactNode; children: ReactNode; footer?: ReactNode; flush?: boolean; className?: string;
}) {
  return (
    <section className={`panel ${className}`}>
      {(title || actions) && (
        <header className="panel-head">
          <div>
            {title && <h2>{title}</h2>}
            {subtitle && <p>{subtitle}</p>}
          </div>
          {actions && <div className="row">{actions}</div>}
        </header>
      )}
      <div className={`panel-body${flush ? " flush" : ""}`}>{children}</div>
      {footer && <footer className="panel-foot">{footer}</footer>}
    </section>
  );
}

export function PageHeader({ title, description, actions }: { title: string; description?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="page-head">
      <div>
        <h1>{title}</h1>
        {description && <p>{description}</p>}
      </div>
      {actions && <div className="page-actions">{actions}</div>}
    </div>
  );
}

export function Stat({ label, value, unit, sub, loading, title }: {
  label: ReactNode; value: ReactNode; unit?: string; sub?: ReactNode; loading?: boolean; title?: string;
}) {
  return (
    <div className="panel stat" title={title}>
      <div className="stat-label">{label}</div>
      {loading ? <div className="skeleton" style={{ height: 28, width: "60%", marginTop: 8 }} /> : (
        <div className="stat-value">{value}{unit && value !== "—" && <small>{unit}</small>}</div>
      )}
      {sub && <div className="stat-sub">{sub}</div>}
    </div>
  );
}

type Tone = "success" | "warning" | "error" | "info" | "accent" | "neutral";

export function Badge({ tone = "neutral", children, dot, title }: { tone?: Tone; children: ReactNode; dot?: boolean; title?: string }) {
  return <span className={`badge ${tone === "neutral" ? "" : tone}`} title={title}>{dot && <span className="dot" />}{children}</span>;
}

export function StatusBadge({ status }: { status: HealthStatus | string }) {
  const tone: Tone = status === "healthy" ? "success" : status === "unhealthy" ? "error" : "neutral";
  const label = status === "healthy" ? "Healthy" : status === "unhealthy" ? "Unhealthy" : "Unknown";
  return <Badge tone={tone} dot>{label}</Badge>;
}

export function ExperimentStatus({ status }: { status: string }) {
  const map: Record<string, Tone> = { completed: "success", running: "accent", stopping: "warning", stopped: "warning", failed: "error", interrupted: "error", created: "neutral" };
  return <Badge tone={map[status] ?? "neutral"} dot>{status.charAt(0).toUpperCase() + status.slice(1)}</Badge>;
}

export function KindTag({ kind }: { kind: "measured" | "simulated" | "estimated" | "configured" }) {
  const titles = {
    measured: "Measured from real requests or probes",
    simulated: "Injected by the simulator (Local Mode network profile)",
    estimated: "Derived estimate, not a direct measurement",
    configured: "Static configuration value",
  };
  return <span className="tag" title={titles[kind]}>{kind}</span>;
}

export function Button({ variant = "secondary", size, icon, loading, children, ...rest }: {
  variant?: "primary" | "secondary" | "ghost" | "danger" | "danger-solid"; size?: "sm"; icon?: ReactNode; loading?: boolean;
} & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  const cls = ["btn", variant === "secondary" ? "" : variant, size ?? "", !children ? "icon" : ""].filter(Boolean).join(" ");
  return (
    <button className={cls} disabled={loading || rest.disabled} {...rest}>
      {loading ? <Loader2 size={14} className="spin" aria-hidden /> : icon}
      {children}
    </button>
  );
}

export function EmptyState({ icon, title, children, action }: { icon?: ReactNode; title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="empty">
      {icon ?? <Inbox size={22} />}
      <h3>{title}</h3>
      {children && <p>{children}</p>}
      {action}
    </div>
  );
}

export function Skeleton({ height = 16, width = "100%" }: { height?: number; width?: number | string }) {
  return <div className="skeleton" style={{ height, width }} />;
}

export function ErrorAlert({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="alert error" role="alert">
      <AlertCircle size={16} />
      <div style={{ flex: 1 }}>{message}</div>
      {onRetry && <button className="btn sm" onClick={onRetry}>Retry</button>}
    </div>
  );
}

export function Note({ children }: { children: ReactNode }) {
  return <p className="note"><Info size={13} />{children}</p>;
}

export function Meter({ value, warnAt = 0.75, badAt = 0.95, label }: { value: number | null | undefined; warnAt?: number; badAt?: number; label?: string }) {
  const v = Math.max(0, Math.min(1, value ?? 0));
  const cls = v >= badAt ? "bad" : v >= warnAt ? "warn" : "";
  return (
    <div className={`meter ${cls}`} role="meter" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(v * 100)} aria-label={label}>
      <span style={{ width: `${v * 100}%` }} />
    </div>
  );
}

export function Segmented<T extends string>({ value, options, onChange, label }: {
  value: T; options: { value: T; label: string }[]; onChange: (v: T) => void; label: string;
}) {
  return (
    <div className="segmented" role="group" aria-label={label}>
      {options.map((o) => (
        <button key={o.value} type="button" aria-pressed={value === o.value} onClick={() => onChange(o.value)}>{o.label}</button>
      ))}
    </div>
  );
}

export function OptionList<T extends string>({ value, options, onChange, label }: {
  value: T; options: { value: T; title: string; description?: string; extra?: ReactNode }[]; onChange: (v: T) => void; label: string;
}) {
  return (
    <div className="option-list" role="radiogroup" aria-label={label}>
      {options.map((o) => (
        <button key={o.value} type="button" role="radio" aria-checked={value === o.value} className="option" onClick={() => onChange(o.value)}>
          <span className="radio" />
          <span style={{ flex: 1, minWidth: 0 }}>
            <span className="option-title">{o.title}</span>
            {o.description && <span className="option-desc" style={{ display: "block" }}>{o.description}</span>}
          </span>
          {o.extra}
        </button>
      ))}
    </div>
  );
}

export function Field({ label, hint, help, children, className = "" }: { label: string; hint?: ReactNode; help?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <div className={`field ${className}`}>
      <label>{label}{hint !== undefined && <span className="hint">{hint}</span>}</label>
      {children}
      {help && <span className="field-help">{help}</span>}
    </div>
  );
}

/* --------------------------------------------------------------- toasts */
interface ToastMsg { id: number; tone: "success" | "error" | "info"; text: string }
const ToastCtx = createContext<(tone: ToastMsg["tone"], text: string) => void>(() => {});

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastMsg[]>([]);
  const next = useRef(1);
  const push = useCallback((tone: ToastMsg["tone"], text: string) => {
    const id = next.current++;
    setItems((s) => [...s.slice(-3), { id, tone, text }]);
    setTimeout(() => setItems((s) => s.filter((t) => t.id !== id)), tone === "error" ? 7000 : 4000);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="toasts" aria-live="polite">
        {items.map((t) => (
          <div key={t.id} className={`toast ${t.tone}`} role={t.tone === "error" ? "alert" : "status"}>
            {t.tone === "success" ? <CheckCircle2 size={16} /> : t.tone === "error" ? <AlertCircle size={16} /> : <Info size={16} />}
            <div style={{ flex: 1 }}>{t.text}</div>
            <button className="btn ghost sm icon" aria-label="Dismiss" onClick={() => setItems((s) => s.filter((x) => x.id !== t.id))}><X size={14} /></button>
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

export function useToast() {
  return useContext(ToastCtx);
}

/* --------------------------------------------------------------- confirm dialog */
export function ConfirmDialog({ open, title, body, confirmLabel = "Confirm", danger, onConfirm, onCancel }: {
  open: boolean; title: string; body: ReactNode; confirmLabel?: string; danger?: boolean; onConfirm: () => void; onCancel: () => void;
}) {
  const confirmRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!open) return;
    confirmRef.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onCancel();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onCancel]);
  if (!open) return null;
  return (
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && onCancel()}>
      <div className="dialog" role="alertdialog" aria-modal="true" aria-labelledby="dlg-title">
        <h2 id="dlg-title">{title}</h2>
        <p>{body}</p>
        <div className="dialog-actions">
          <button className="btn" onClick={onCancel}>Cancel</button>
          <button ref={confirmRef} className={`btn ${danger ? "danger-solid" : "primary"}`} onClick={onConfirm}>{confirmLabel}</button>
        </div>
      </div>
    </div>
  );
}

/** Hook returning [dialogElement, ask(opts) => Promise<boolean>] */
export function useConfirm(): [ReactNode, (o: { title: string; body: ReactNode; confirmLabel?: string; danger?: boolean }) => Promise<boolean>] {
  const [state, setState] = useState<{ title: string; body: ReactNode; confirmLabel?: string; danger?: boolean; resolve: (v: boolean) => void } | null>(null);
  const ask = useCallback((o: { title: string; body: ReactNode; confirmLabel?: string; danger?: boolean }) =>
    new Promise<boolean>((resolve) => setState({ ...o, resolve })), []);
  const close = (v: boolean) => {
    state?.resolve(v);
    setState(null);
  };
  const el = (
    <ConfirmDialog open={!!state} title={state?.title ?? ""} body={state?.body} confirmLabel={state?.confirmLabel}
      danger={state?.danger} onConfirm={() => close(true)} onCancel={() => close(false)} />
  );
  return [el, ask];
}
