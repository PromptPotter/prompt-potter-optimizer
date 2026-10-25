"use client";
// The disabled controls are INTENTIONAL placeholders (`docs/specs/chat-foundation.md`): never hide them.
import { useEffect, useRef, useState } from "react";
import { useHardSamples } from "@/lib/hard-samples";
import { useViewedCourse, useViewedLineage } from "@/lib/lineage";
import { useCycleStream } from "@/lib/poll";
import { useWorkspace } from "@/lib/workspace";
import { entryAlone } from "@/lib/derivations";
import { useIngest } from "@/lib/ingest-flow";
import { IngestComposer, useIngestItems } from "@/components/ingest/IngestConversation";
import type { HierarchyCourse } from "@/lib/api";
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
  const { viewedPath, cycleId, leafCycleId, at } = useWorkspace();
  const { composing } = useIngest();
  const thread = useThread<HierarchyCourse>();

  const course = useViewedCourse();
  const { maskLabel } = useViewedLineage();

  // The served `attached`, NOT `isLive`, which is false at the gate.
  const attached = dash?.producer.attached === true;
  // The course entry is frozen whole on its own detach, so the ending is one served read.
  const liveCourse = course?.state.attached ? course.address : null;
  const [prevLiveCourse, setPrevLiveCourse] = useState(liveCourse);
  if (liveCourse !== prevLiveCourse) {
    setPrevLiveCourse(liveCourse);
    // The identity check stops a course switch filing another course under this one's ending.
    if (prevLiveCourse && !liveCourse && course?.address === prevLiveCourse) {
      // A mask or a moment re-reads the stats; the frozen item says which, never passes as the record.
      const readUnder = maskLabel || (at !== null ? "a past moment" : "");
      thread.appendRun(course.address, {
        ...entryAlone(course),
        caveat: readUnder ? `Read under ${readUnder}` : course.caveat,
      });
    }
  }

  const live = useCycleEvents(viewedPath);
  // `composing` drops both, so a fresh thread is not drawn over the bound cycle's run.
  const mounted: ThreadItem<HierarchyCourse>[] = [];
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
          lives={course?.facts.standing?.lives ?? null}
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
  const items: ThreadItem<HierarchyCourse>[] = [
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
            <Thread items={items} renderRun={(course) => <RunSummaryItem course={course} />} />
            <IngestComposer />
          </div>
        </div>
      </div>
    </div>
  );
}
