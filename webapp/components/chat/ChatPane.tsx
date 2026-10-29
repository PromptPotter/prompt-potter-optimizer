"use client";
// The disabled controls are INTENTIONAL placeholders (`docs/specs/chat-foundation.md`): never hide them.
import { useEffect, useRef, useState } from "react";
import { useHardSamples } from "@/lib/hard-samples";
import { useViewedCourse } from "@/lib/lineage";
import { useWorkspace } from "@/lib/workspace";
import { entryAlone } from "@/lib/derivations";
import { encodeCyclePath } from "@/lib/ids";
import { NEW_CHECKIN } from "@/lib/hooks/useIngestFlow";
import { useIngest } from "@/lib/ingest-flow";
import { IngestComposer, useIngestItems } from "@/components/ingest/IngestConversation";
import type { HierarchyCourse } from "@/lib/api";
import { NodeDetail } from "@/components/shell/node-surface/NodeDetail";
import { PipelineStack } from "@/components/dashboard/pipeline/PipelineStack";
import { useSelection } from "@/lib/SelectionContext";
import { useCycleEvents } from "@/lib/hooks/useCycleEvents";
import {
  checkinThreadKey,
  runThreadKey,
  useThreads,
  type ThreadItem,
} from "@/lib/chat/thread";
import { Thread } from "@/components/chat/Thread";
import { LiveSegment } from "@/components/chat/LiveSegment";
import { RunCard, RunSummaryItem } from "@/components/chat/RunCard";

// The pane renders ONE subject's conversation: the viewed check-in's, or the viewed run's.
export function ChatPane() {
  const { datasetName } = useHardSamples();
  const { viewedPath, cycleId, leafCycleId } = useWorkspace();
  const { subject } = useIngest();
  const threads = useThreads<HierarchyCourse>();
  const runKey = viewedPath ? runThreadKey(encodeCyclePath(viewedPath)) : null;
  const threadKey =
    subject === null && runKey !== null
      ? runKey
      : checkinThreadKey(subject === null || subject === NEW_CHECKIN ? null : subject);
  const stored = threads.of(threadKey);

  const course = useViewedCourse();

  // The served `attached`, NOT `isLive`, which is false at the gate. The course entry is frozen
  // whole on its own detach, so the ending is one served read.
  const liveCourse = course?.state.attached ? course.address : null;
  const [prevLiveCourse, setPrevLiveCourse] = useState(liveCourse);
  if (liveCourse !== prevLiveCourse) {
    setPrevLiveCourse(liveCourse);
    // The identity check stops a course switch filing another course under this one's ending.
    if (runKey && prevLiveCourse && !liveCourse && course?.address === prevLiveCourse) {
      // Read under a mask or a moment, the served caveat already names which.
      threads.appendRun(runKey, course.address, entryAlone(course));
    }
  }

  const live = useCycleEvents(viewedPath);
  const mounted: ThreadItem<HierarchyCourse>[] = [];
  if (subject === null && leafCycleId) {
    mounted.push({
      id: "live",
      kind: "block",
      node: (
        <LiveSegment
          notices={live.notices}
          status={live.status}
          listening={live.connected && liveCourse !== null}
          decision={live.decision}
          lives={course?.facts.standing?.lives ?? null}
        />
      ),
    });
  }
  if (subject === null && cycleId) {
    mounted.push({
      id: "run-card",
      kind: "block",
      node: <RunCard />,
    });
  }
  const ingestItems = useIngestItems(stored.length === 0);
  const items: ThreadItem<HierarchyCourse>[] =
    subject === null
      ? [...stored, ...mounted]
      : [...ingestItems.head, ...stored, ...ingestItems.tail];

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
