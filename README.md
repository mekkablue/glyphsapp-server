# GlyphsApp Server

A [Glyphs](https://glyphsapp.com/) plug‑in that runs a tiny local HTTP server inside the app, so that a link in an HTML file (or an email, a wiki, a spec, a proofing sheet …) can open an Edit tab in your frontmost font.

Click a link like this:

```
http://127.0.0.1:49152/frontmostfont/newtab/ABC
```

… and Glyphs opens a new tab showing **ABC** in the current font.

## Why a local server (and not a `glyphsapp://` URL scheme)?

A custom URL scheme is the “textbook” macOS way, but it is awkward to ship as a plug‑in: a General Plugin is a bundle loaded *into* the Glyphs process and has no identity of its own with Launch Services, so registering a scheme would require a separate helper `.app` plus some cross‑process hand‑off (a distributed notification or an Apple Event) to get the message into Glyphs.

A localhost server removes both of those hard parts:

|                                             | `glyphsapp://` scheme        | localhost server (this plug‑in) |
| ------------------------------------------- | ---------------------------- | ------------------------------- |
| Launch Services registration                | required (needs a real `.app`) | **none**                        |
| Separate helper app to sign / notarize      | yes                          | **no** – lives in the plug‑in   |
| Cross‑process relay to Glyphs               | yes                          | **no** – the plug‑in gets the request directly |
| Works from any browser / HTML / Markdown    | yes                          | yes                             |
| Install story                               | helper app **and** plug‑in   | one plug‑in                     |

The server binds to `127.0.0.1` only, so it is reachable from your own machine, not from the network.

## Installation

**From source (developer mode):**

1. Double‑click `GlyphsApp Server.glyphsPlugin`. Glyphs offers to install it.
2. Restart Glyphs.
3. Open a font. The server starts automatically and listens on port `49152`.

You can confirm it is running via **Edit → GlyphsApp Server**, which shows the current address, and by the line it prints to the Macro window on launch:

```
GlyphsApp Server: listening on http://127.0.0.1:49152/
```

## Usage

### URL format

```
http://127.0.0.1:<port>/frontmostfont/newtab/<text>
```

- `frontmostfont` — the font selector. Currently only the frontmost font is supported.
- `newtab` — the command. Opens a new Edit tab.
- `<text>` — the glyphs/characters to show. URL‑encode anything special.

The text can also be passed as a query parameter, which is more robust for strings containing slashes or spaces:

```
http://127.0.0.1:49152/frontmostfont/newtab/?text=Hamburgevons
```

Both plain characters (`ABC`) and Glyphs’ slash notation (`/a /b /c`) work, exactly as if you had typed them into the tab’s text field.

### From a plain link

```html
<a href="http://127.0.0.1:49152/frontmostfont/newtab/ABC">edit ABC</a>
```

Clicking navigates the browser to the server, which answers with a short text confirmation. Simple, but it leaves the browser sitting on that response page.

### From `fetch()` (no navigation)

For a smoother experience, call the server with `fetch()` so the page never navigates:

```html
<a href="#" onclick="fetch('http://127.0.0.1:49152/frontmostfont/newtab/?text=ABC'); return false;">edit ABC</a>
```

A complete, ready‑to‑open example is in [`example/demo.html`](example/demo.html).

> **Note on HTTPS pages:** the server speaks plain HTTP. A page served over `https://` cannot call an `http://` endpoint (mixed content). Open the HTML from a `file://` or `http://` origin, or run your proofing tool over HTTP.

## Configuration

The port defaults to `49152`. To change it, set the preference in the Macro window and restart Glyphs:

```python
Glyphs.defaults["com.mekkablue.GlyphsAppServer.port"] = 50000
```

## How it works

- On `start()`, the plug‑in launches a threaded `HTTPServer` bound to `127.0.0.1` on a background thread.
- Incoming requests are parsed on that background thread, then the actual Glyphs work (`font.newTab(text)`) is dispatched to the **main thread** via `performSelectorOnMainThread_…`, because the Glyphs API is not thread‑safe.
- The server replies with a short `text/plain` message (and permissive CORS headers, so `fetch()` from local proofing pages works).

The bundle follows the standard Glyphs Python‑plugin layout:

```
GlyphsApp Server.glyphsPlugin/
└── Contents/
    ├── Info.plist            NSPrincipalClass = GlyphsAppServer, PyMainFileNames = [plugin.py]
    ├── PkgInfo               BNDL????
    ├── MacOS/
    │   └── plugin            generic universal (arm64 + x86_64) Python loader (CFBundleExecutable)
    └── Resources/
        └── plugin.py         the plug‑in itself
```

`MacOS/plugin` is the generic py2app loader shared by every Glyphs Python plugin. It reads `PyMainFileNames` from `Info.plist` and runs `Resources/plugin.py` directly (no separate `main.py` bootstrap). It must be present, executable, and **universal** — a loader lacking an `arm64` slice makes Glyphs on Apple Silicon report *“it doesn’t contain a version for the current architecture,”* and a missing loader makes it report *“its executable couldn’t be located.”*

## Endpoints

| Method | Path                                   | Effect                                             |
| ------ | -------------------------------------- | -------------------------------------------------- |
| `GET`  | `/`                                    | Health check — returns a “running” message.        |
| `GET`  | `/frontmostfont/newtab/<text>`         | Opens a new tab with `<text>` in the frontmost font. |
| `GET`  | `/frontmostfont/newtab/?text=<text>`   | Same, with the text as a query parameter.          |

## Requirements

- Glyphs 3 (Python 3 plug‑in).
- A font open in Glyphs when a request arrives (otherwise the server answers with *“No font open”*).

## License

Apache License 2.0.
