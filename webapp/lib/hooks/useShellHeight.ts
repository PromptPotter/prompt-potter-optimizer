// What floats over the panes reserves no space of its own: it publishes its height on `.shell`,
// for the spacer or offset that clears it. A ref callback, so the first height lands in the commit
// that mounts the node: no frame draws a pane under a float not yet reserved.

import { useCallback } from "react";

export function useShellHeight(name: `--${string}`) {
  return useCallback(
    (node: HTMLElement | null) => {
      const shell = node?.closest<HTMLElement>(".shell");
      if (!node || !shell) return;
      const publish = () => shell.style.setProperty(name, `${node.offsetHeight}px`);
      publish();
      const observer = new ResizeObserver(publish);
      observer.observe(node);
      return () => {
        observer.disconnect();
        shell.style.removeProperty(name);
      };
    },
    [name],
  );
}
