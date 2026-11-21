"use client";

import { cva } from "class-variance-authority";
import { clsx } from "clsx";
import { X } from "lucide-react";
import { useEffect, useRef } from "react";
import { twMerge } from "tailwind-merge";

export function cn(...values) {
  return twMerge(clsx(values));
}

const button = cva(
  "inline-flex min-h-9 items-center justify-center gap-2 rounded-md border px-3.5 py-2 text-sm font-medium leading-none transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-45",
  {
    variants: {
      variant: {
        default: "border-primary bg-primary text-white hover:bg-indigo-700",
        outline: "border-border bg-white text-foreground hover:bg-muted",
        ghost:
          "border-transparent bg-transparent text-foreground hover:bg-muted",
        danger: "border-red-700 bg-red-700 text-white hover:bg-red-800",
      },
    },
    defaultVariants: { variant: "default" },
  },
);

export function Button({ className, variant, type = "button", ...props }) {
  return (
    <button
      type={type}
      data-slot="button"
      className={cn(button({ variant }), className)}
      {...props}
    />
  );
}

const field =
  "w-full rounded-md border border-border bg-white px-3 py-2.5 text-sm text-foreground shadow-sm outline-none transition focus-visible:border-primary focus-visible:ring-2 focus-visible:ring-indigo-100 placeholder:text-subtle";

export function Input({ className, ...props }) {
  return (
    <input data-slot="input" className={cn(field, className)} {...props} />
  );
}

export function Textarea({ className, ...props }) {
  return (
    <textarea
      className={cn(field, "min-h-24 resize-y", className)}
      {...props}
    />
  );
}

export function Select({ className, children, ...props }) {
  return (
    <select className={cn(field, "select-field", className)} {...props}>
      {children}
    </select>
  );
}

export function Field({ label, hint, children }) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
      {hint && <small>{hint}</small>}
    </label>
  );
}

export function Badge({ children, tone = "neutral" }) {
  return <span className={cn("badge", "badge-" + tone)}>{children}</span>;
}

export function Panel({ title, action, children, className }) {
  return (
    <section className={cn("panel", className)}>
      {title && (
        <div className="panel-header">
          <h2>{title}</h2>
          {action}
        </div>
      )}
      {children}
    </section>
  );
}

export function Empty({ icon: Icon, title, children }) {
  return (
    <div className="empty">
      {Icon && (
        <span className="empty-icon">
          <Icon size={18} aria-hidden="true" />
        </span>
      )}
      <strong>{title}</strong>
      {children && <p>{children}</p>}
    </div>
  );
}

export function Notice({ error }) {
  return error ? (
    <p className="notice" role="alert">
      {error}
    </p>
  ) : null;
}

export function Dialog({ title, onClose, children }) {
  const ref = useRef(null);
  useEffect(() => {
    const dialog = ref.current;
    dialog.showModal();
    return () => dialog.close();
  }, []);
  return (
    <dialog
      ref={ref}
      className="dialog"
      aria-label={title}
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
    >
      <div className="panel-header">
        <h2>{title}</h2>
        <Button variant="ghost" aria-label="Close dialog" onClick={onClose}>
          <X size={17} />
        </Button>
      </div>
      <div className="dialog-body">{children}</div>
    </dialog>
  );
}
