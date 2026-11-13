// The disabled controls are INTENTIONAL placeholders (`docs/specs/roadmap.md` § Host assistant): never hide them.
import { useState } from "react";
import { useLeafDatasetName } from "@/lib/hooks/useLeafDatasetName";
import { useViewedCourse } from "@/lib/lineage";
import { useWorkspace } from "@/lib/workspace";
import { entryAlone } from "@/lib/derivations";
import { NEW_CHECKIN } from "@/lib/hooks/useIngestFlow";
import { useCheckinSubject } from "@/lib/state/ingest-flow";
import { IngestComposer, useIngestItems } from "@/components/ingest/IngestConversation";
import type { HierarchyCourse } from "@/lib/api/types";
import { PipelineStack } from "@/components/dashboard/pipeline/PipelineStack";
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
  const datasetName = useLeafDatasetName();
  const { viewed } = useWorkspace();
  const viewedPath = viewed?.path ?? null;
  const subject = useCheckinSubject();
  const threads = useThreads<HierarchyCourse>();
  const runKey = viewed ? runThreadKey(viewed.address) : null;
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
  if (subject === null && viewed) {
    mounted.push({
      id: "live",
      kind: "block",
      node: (
        <LiveSegment
          notices={live.notices}
          status={live.status}
          listening={live.connected && liveCourse !== null}
          decision={live.decision}
        />
      ),
    });
  }
  if (subject === null && viewed) {
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

  return (
    <div className="content chat-content" id="content-chat">
      {/* A run's alone: over a check-in its steps draw the draft's pipeline. */}
      {subject === null && viewedPath && (
        <div className="wf-hero">
          <PipelineStack datasetName={datasetName} />
        </div>
      )}

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
