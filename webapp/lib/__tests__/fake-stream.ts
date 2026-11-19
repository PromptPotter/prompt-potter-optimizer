import { SERVED_CONTRACT, type FeedVersion } from "@/lib/api/types";

// A subject's *n*th version in one serving process (`cycle_reads.py::FeedVersion`).
export const feedVersion = (n: number, process = "p1"): FeedVersion => ({ process, n });

// The browser's `EventSource`, driven by a test: the server's frames, each stamped as the server
// stamps it (`cycle_reads.py::stamped_frames`), a drop, a refusal.
export class FakeStream {
  static CLOSED = 2;
  static opened: FakeStream[] = [];
  readyState = 1;
  onopen: (() => void) | null = null;
  onmessage: ((ev: MessageEvent<string>) => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(readonly url: string) {
    FakeStream.opened.push(this);
  }
  close(): void {
    this.readyState = FakeStream.CLOSED;
  }
  send(body: object, contract: string = SERVED_CONTRACT): void {
    this.onmessage?.({ data: JSON.stringify({ ...body, contract }) } as MessageEvent<string>);
  }
  // A feed frame: a subject moved to its *n*th version.
  frame(subject: string, n: number, process?: string): void {
    this.send({ subject, version: feedVersion(n, process) });
  }
}
