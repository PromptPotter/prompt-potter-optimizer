import type { ReactNode } from "react";
import { cx } from "@/lib/cx";
import { Chip } from "./Chip";
import { IconMore } from "./icons";
import { Popover } from "./Popover";
import m from "./Menu.module.css";
import s from "./Toolbar.module.css";

export function Toolbar({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cx(s.bar, className)}>{children}</div>;
}

export function ToolbarSep() {
  return <span className={s.sep} aria-hidden="true" />;
}

export function ToolbarSpacer() {
  return <span className={s.spacer} aria-hidden="true" />;
}

// A press that picks: an opener (`aria-expanded`) keeps the fold up for what it opens.
const picks = (target: EventTarget) => {
  const hit = (target as Element).closest("button, a");
  return hit !== null && !hit.hasAttribute("aria-expanded");
};

// A header's trailing actions: in the row on a wide screen, behind its one ⋯ on a phone, so the
// header stays ONE row. `menu` is the ⋯'s own items, under the folded actions; without it the ⋯ is
// a phone's alone. A pick inside closes the ⋯.
export function ToolbarFold({
  label,
  hint,
  lit = false,
  actions,
  menu,
}: {
  label: string;
  hint?: string;
  lit?: boolean;
  actions: ReactNode;
  menu?: ReactNode;
}) {
  return (
    <>
      <span className={cx(!menu && s.phoneOnly)}>
        <Popover
          align="right"
          renderTrigger={({ open, toggle }) => (
            <Chip
              icon
              on={open || lit}
              expanded={open}
              ariaLabel={label}
              title={hint}
              onClick={toggle}
            >
              <IconMore />
            </Chip>
          )}
        >
          {({ close }) => (
            // On the way up, once the pick's own handler ran, and in the same event: a close on the way
            // down unmounts the pick before it fires, and a deferred one leaves the fold up over it. It
            // hears its buttons' clicks, which a key press on one fires too, and is no control itself.
            <div role="presentation" onClick={(e) => picks(e.target) && close()}>
              <div className={s.folded} role="group" aria-label={label}>
                {actions}
              </div>
              {menu && (
                <div className={m.menu} role="menu">
                  {menu}
                </div>
              )}
            </div>
          )}
        </Popover>
      </span>
      <span className={s.inline}>{actions}</span>
    </>
  );
}
