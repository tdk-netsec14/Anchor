/**
 * The Anchor mark: an anchor glyph drawn as strokes, so it inherits
 * currentColor in both themes instead of shipping two raster assets.
 *
 * Kept out of the sidebar because the landing page and login screen render it
 * too, and those are server-rendered.
 */
export function Mark({ className }: { className?: string }) {
  return (
    <span
      className={
        className ??
        "flex size-6 items-center justify-center rounded-md bg-accent text-accent-fg"
      }
      aria-hidden="true"
    >
      <svg viewBox="0 0 24 24" className="size-3.5" fill="none" stroke="currentColor" strokeWidth="2.2">
        <circle cx="12" cy="4.6" r="1.9" />
        <path
          d="M12 6.5V20M6 12H18M6 12v1.5A5.4 5.4 0 0 0 11.4 19M18 12v1.5A5.4 5.4 0 0 1 12.6 19"
          strokeLinecap="round"
        />
      </svg>
    </span>
  );
}
