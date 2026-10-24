"use client";
// The disabled controls are INTENTIONAL placeholders (`docs/specs/chat-foundation.md`): never hide them.
import { useEffect, useRef, useState } from "react";
import { useHardSamples } from "@/lib/hard-samples";
import { useViewedCourse } from "@/lib/lineage";
import { useCycleStream } from "@/lib/poll";
import { useWorkspace } from "@/lib/workspace";
import { useIngest } from "@/lib/ingest-flow";
import { IngestComposer, useIngestItems } from "@/components/ingest/IngestConversation";
import { runSummary, type RunSummary } from "@/lib/derivations";
import { NodeDetail } from "@/components/shell/node-surface/NodeDetail";
import { PipelineStack } from "@/components/dashboard/pipeline/PipelineStack";
import { useSelection } from "@/lib/SelectionContext";
import { useCycleEvents } from "@/lib/chat/useCycleEvents";
import { useThread, type ThreadItem } from "@/lib/chat/thread";
import { Thread } from "@/components/chat/Thread";
import { LiveSegment } from "@/components/chat/LiveSegment";
import { RunCard, RunSummaryItem } from "@/components/chat/RunCard";

export function ChatPane() {
  const { datasetName } = useHardSamples();
  const { dash } = useCycleStream();
  const { viewedPath, cycleId, leafCycleId } = useWorkspace();
  const { composing } = useIngest();
  const thread = useThread<RunSummary>();

  const course = useViewedCourse();

  // The served `attached`, NOT `isLive`, which is false at the gate.
  const attached = dash?.producer.attached === true;
  // Frozen off the COURSE's detach, so the ending's word and next step are that same read's.
  const liveCycleKey = cycleId && course?.state.producer.attached ? cycleId : null;
  const [prevLiveCycle, setPrevLiveCycle] = useState(liveCycleKey);
  if (liveCycleKey !== prevLiveCycle) {
    setPrevLiveCycle(liveCycleKey);
    const ended = runSummary(dash, course);
    // The identity check stops a cycle switch filing the new cycle's numbers under the old one's ending.
    if (prevLiveCycle && !liveCycleKey && ended?.cycleId === prevLiveCycle) {
      thread.appendRun(ended.cycleId, ended);
    }
  }

  const live = useCycleEvents(viewedPath);
  // `composing` drops both, so a fresh thread is not drawn over the bound cycle's run.
  const mounted: ThreadItem<RunSummary>[] = [];
  if (!composing && leafCycleId) {
    mounted.push({
      id: "live",
      kind: "block",
      node: (
        <LiveSegment
          notices={live.notices}
          status={live.status}
          listening={live.connected && attached}
          decision={live.decision}
          lives={dash?.run_standing?.lives ?? null}
        />
      ),
    });
  }
  if (!composing && cycleId) {
    mounted.push({
      id: "run-card",
      kind: "block",
      node: <RunCard />,
    });
  }
  const ingestItems = useIngestItems(thread.items.length === 0 && mounted.length === 0);
  const items: ThreadItem<RunSummary>[] = [
    ...ingestItems.head,
    ...thread.items,
    ...ingestItems.tail,
    ...mounted,
  ];

  const { node: selectedNode, setSelectionForNode } = useSelection();
  // The remote floats above the composer, so its height is published to the shell the remote hangs off.
  const paneRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const row = paneRef.current?.querySelector<HTMLElement>(".chat-input-row");
    const shell = paneRef.current?.closest<HTMLElement>(".shell");
    if (!row || !shell) return;
    const publish = new ResizeObserver(() =>
      shell.style.setProperty("--chat-composer-h", `${row.offsetHeight}px`),
    );
    publish.observe(row);
    return () => {
      publish.disconnect();
      shell.style.removeProperty("--chat-composer-h");
    };
  }, []);

  return (
    <div className="content chat-content" id="content-chat" ref={paneRef}>
      <div className="wf-hero">
        <PipelineStack datasetName={datasetName} />
        {selectedNode && (
          <div className="wf-hero-detail">
            <NodeDetail node={selectedNode} onClose={() => setSelectionForNode(null)} />
          </div>
        )}
      </div>

      <div className="chat-grid">
        <div className="chat-panel">
          <div className="ingest-conversation">
            <Thread items={items} renderRun={(summary) => <RunSummaryItem summary={summary} />} />
            <IngestComposer />
          </div>
        </div>
      </div>
    </div>
  );
}
