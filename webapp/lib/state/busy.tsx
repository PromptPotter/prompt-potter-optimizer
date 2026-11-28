import { createContext, useContext, useMemo, useState, type ComponentProps } from "react";

// A read in flight is a state its pane reports: `useRead` marks the nearest pane `aria-busy` while
// its read flies, whatever the pane draws meanwhile; outside every pane, the document.
type Mark = () => () => void;

const held = new WeakMap<Element, number>();

function markOn(el: () => Element | null): Mark {
  return () => {
    const e = el();
    if (e === null) return () => {};
    const bump = (by: number) => {
      const n = (held.get(e) ?? 0) + by;
      held.set(e, n);
      if (n > 0) e.setAttribute("aria-busy", "true");
      else e.removeAttribute("aria-busy");
    };
    bump(1);
    return () => bump(-1);
  };
}

const BusyContext = createContext<Mark>(
  markOn(() => (typeof document === "undefined" ? null : document.documentElement)),
);

export function useBusyMark(): Mark {
  return useContext(BusyContext);
}

// A view column: every read under it reports on it.
export function BusyPane({ children, ...props }: ComponentProps<"div">) {
  const [el, setEl] = useState<HTMLDivElement | null>(null);
  const mark = useMemo(() => markOn(() => el), [el]);
  return (
    <div {...props} ref={setEl}>
      <BusyContext.Provider value={mark}>{children}</BusyContext.Provider>
    </div>
  );
}
