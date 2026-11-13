const CONTROL = "button, a[href], input, select, textarea, summary, [tabindex]";

// A press is its frame's own when it lands in the frame's DOM on no live control but the frame:
// a control inside answers for itself, and so does a portal the frame opened (a menu), whose press
// reaches the frame through the React tree alone. A disabled control taps through to the frame.
export function ownPress(frame: Element, target: EventTarget): boolean {
  if (!(target instanceof Element) || !frame.contains(target)) return false;
  const control = target.closest(CONTROL);
  return (
    control === null ||
    control === frame ||
    !frame.contains(control) ||
    control.matches(":disabled")
  );
}
