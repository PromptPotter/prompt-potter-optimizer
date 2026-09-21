# Vendor marks — the gap assets

Brand marks for model vendors that the `simple-icons` devDependency does **not** carry. Everything
else is read straight out of `node_modules/simple-icons/icons/` at generate time, so this
directory holds only the exceptions.

`scripts/build_vendor_marks.py` reads this directory first, then falls back to `simple-icons`, and
writes `webapp/components/ui/vendor-marks.generated.ts`. The gate's `vendor-marks` check fails if
that file and the generator disagree, so a mark can never drift from its source.

## Why these are files rather than another dependency

The one package that covers every AI vendor is `@lobehub/icons`, and it peer-depends on **Ant
Design** — an entire second design system in a repo that has none. Its asset-only sibling
`@lobehub/icons-static-svg` ships raw `.svg` with no JS entry point, so consuming it would mean
adding an SVG loader. Four files, reviewed once, cost less than either.

| File | Source | Licence |
|---|---|---|
| `openai.svg` | [`@lobehub/icons-static-svg@1.95.1`](https://www.npmjs.com/package/@lobehub/icons-static-svg) `icons/openai.svg` | MIT |
| `x-ai.svg` | same package, `icons/xai.svg` | MIT |
| `z-ai.svg` | same package, `icons/zhipu.svg` (Z.ai is Zhipu's brand) | MIT |
| `upstage.svg` | same package, `icons/upstage.svg` | MIT |

`simple-icons` dropped its OpenAI mark, and never carried xAI, Zhipu or Upstage — that is the whole
reason this directory exists. Re-check after a `simple-icons` upgrade: if it gains one of these,
delete the file here and give the vendor an `si` slug in the generator instead.

## What a file here must satisfy

The generator refuses anything else, so these are checked, not conventions:

- `viewBox="0 0 24 24"` — every mark shares one box, which is what lets the component size them
  against each other.
- Every fill is `currentColor` (or absent). A mark carrying its own colours would flatten into a
  silhouette of a logo rather than the logo, and the vendor's ink is applied from one place.

Several `<path>` elements are fine — that is a drawing detail, not a second colour.

## Trademarks

These marks are the trademarks of their respective owners. They are used here to identify whose
model a campaign ran on, which is what a trademark is for. They are not a claim of affiliation or
endorsement.
