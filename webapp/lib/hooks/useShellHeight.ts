// What floats over the panes reserves no space of its own: it publishes its height on `.shell`,
// for the spacer or offset that clears it.

import { useEffect, useState } from "react";

export function useShellHeight(name: `--${string}`) {
  const [node, setNode] = useState<HTMLElement | null>(null);
  useEffect(() => {
    const shell = node?.closest<HTMLElement>(".shell");
    if (!node || !shell) return;
    const publish = new ResizeObserver(() =>
      shell.style.setProperty(name, `${node.offsetHeight}px`),
    );
    publish.observe(node);
    return () => {
      publish.disconnect();
      shell.style.removeProperty(name);
    };
  }, [node, name]);
  return setNode;
}
