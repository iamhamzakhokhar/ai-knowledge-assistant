# Knowledge assistant widget

A TypeScript Web Component with Shadow DOM and no runtime dependencies. This first
setup preview opens a branded panel and clearly states that messaging is not
connected yet. It does not provide chat replies or a send action.

## Build

From the repository root, after installing the workspace dependencies:

```sh
pnpm --filter @aka/widget typecheck
pnpm --filter @aka/widget build
```

TypeScript emits one self-contained classic script at `dist/widget.js`. The source
uses an IIFE and has no imports, so no bundler or module loader is needed.

## One-script installation preview

Serve the built `dist/widget.js` from your own static server, then add this script
tag to a test page. Replace `/widget.js` with the URL where you serve the file.

```html
<script
  src="/widget.js"
  data-greeting="Welcome! How can we help?"
  data-brand-color="#0f766e"
  defer
></script>
```

The script adds one `<aka-chat-widget>` to the page. It stays closed until the
visitor opens it. Repeated script tags do not add duplicate widgets.

## Manual custom element

To choose where the custom element is declared or update its attributes yourself,
disable automatic mounting:

```html
<aka-chat-widget
  greeting="Welcome to the setup preview."
  brand-color="#0f766e"
></aka-chat-widget>
<script src="/widget.js" data-auto-mount="false" defer></script>
```

The widget floats at the lower right regardless of its position in the document.
Changes to `greeting` and `brand-color` are reflected after mounting. Greeting text
is rendered as text, never HTML, and is limited to 280 characters. The brand color
is validated with `CSS.supports("color", value)` and falls back to `#2563eb` when
invalid. Branding decorates the border; readable button/text colors stay fixed.

The launcher exposes its expanded state, opening moves focus to the close button,
and Escape within the widget closes the panel and restores focus to the launcher.
The panel is non-modal, supports narrow screens, and respects reduced-motion
preferences. Full browser and accessibility checks belong to the repository's
verification suite before this preview becomes a release.
