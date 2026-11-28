import { useRef, useState, type ComponentProps } from "react";
import { PromptFieldsEditor, type PromptFieldsHandle } from "./PromptFieldsEditor";
import { SnippetLibrary } from "./SnippetLibrary";

type Props = Omit<ComponentProps<typeof PromptFieldsEditor>, "ref" | "target" | "onTarget" | "folded">;

// One node's prompt with the snippet library under it: browsed beside a banked prompt, a tap lands a
// snippet in the field last opened where the prompt is editable.
export function PromptWithLibrary(props: Props) {
  const editable = !props.readOnly && props.onApply != null;
  const [opened, setField] = useState<string | null>(null);
  // The field last opened while the node still has it, else its first: a field gone takes nothing.
  const field = opened !== null && props.fields.includes(opened) ? opened : (props.fields[0] ?? null);
  const editor = useRef<PromptFieldsHandle>(null);
  return (
    // Stacked on a phone; side by side on a wide screen, the library beside the prompt it feeds.
    <div className="prompt-lib">
      <PromptFieldsEditor
        {...props}
        ref={editor}
        target={editable ? field : null}
        onTarget={setField}
        folded={editable}
      />
      {field === null ? null : (
        <SnippetLibrary
          field={field}
          onPick={editable ? (s) => editor.current?.insert(field, s.text) : undefined}
        />
      )}
    </div>
  );
}
