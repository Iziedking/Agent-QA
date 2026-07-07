# Agent QA brand kit

A small, consistent kit so every surface (the bench, the docs, a social card, a
listing) looks like the same product. Everything here matches what already ships
in `web/index.html`.

## The idea in one line

**Proof, not trust.** A server asks your agent to trust it. Agent QA checks it
instead. The visual language is a clean grey terminal: measured, precise, quiet.

## Logo

The mark is a custom cursive "A" sealed inside the bench panel: a rounded square
with a thin inner border and a single flowing, monoline letter. The "A" is Agent
QA, a softer script that offsets the machined Orbitron wordmark; the panel is the
bench that produces the grade. It shares its lineage with the giant "Q" and "A"
watermarks on the page.

- `web/favicon.svg` — the mark on its own, tuned to stay legible down to 16 px.
- `web/logo.svg` — the full lockup: mark, the `AGENT·QA` wordmark, and the
  `PROOF, NOT TRUST` tagline underneath.

The favicon is also embedded directly in the page head as a base64 SVG, so the
tab icon works with no extra request.

### Using the logo

- Keep clear space around the mark equal to at least half its height.
- The mark may be used alone (favicon, avatar, social square). The wordmark
  should not be used without the mark in the same lockup.
- Do not recolour the check to a brand-foreign hue, stretch the mark, add a drop
  shadow, or place it on a busy background. It sits on the ink background or a
  panel, nothing else.

## Colour

The world is grey on near-black. Colour is spent only where it carries meaning:
the phosphor accent for the live signal, and a single red reserved for failure.

| Token          | Hex       | Role                                              |
| -------------- | --------- | ------------------------------------------------- |
| `--ink`        | `#0a0b0c` | Page background                                   |
| `--ink-2`      | `#0d0f11` | Inset background (fields, console)                |
| `--panel`      | `#101315` | Cards and panels                                  |
| `--inset`      | `#161a1c` | Chips, buttons, code blocks                       |
| `--line`       | `#2f363a` | Default borders                                   |
| `--line-dim`   | `#1c2225` | Quiet dividers                                    |
| `--line-bright`| `#3d464b` | A lit border                                      |
| `--text`       | `#e9ecee` | Primary text                                      |
| `--dim`        | `#9aa3a8` | Secondary text                                    |
| `--faint`      | `#656d72` | Labels, captions                                  |
| `--accent`     | `#aeb7bd` | Grey phosphor: the live signal, the check         |
| `--accent-dim` | `#767e84` | Hover and secondary accent                        |
| `--g-f`        | `#cf6f6f` | Failure only. The single colour cue in the system |

Grade bands step from light to mid grey (`#d6dde1`, `#b9c2c7`, `#9aa3a8`,
`#7f878c`), with failure taking the red above.

## Type

Four faces, each with one job.

- **Orbitron** (`--digital`) — the display face. Wordmark, grade seal, score
  counters. Weights 600 to 800.
- **IBM Plex Mono** (`--mono`) — labels, tool names, chips, the readout voice.
  This is the terminal's own type.
- **Saira** (`--sans`) — body copy and the pitch. Weights 300 to 700.
- **Fraunces** — serif headings inside the docs, for a moment of editorial calm
  against the mono.

Set labels in mono, uppercase, with wide tracking. Set prose in Saira. Reserve
Orbitron for numbers and the wordmark, never for paragraphs.

## Voice

Plain, exact, and free of hype. No em dashes, no "and/or" shortcuts, proper
punctuation. Say what the tool did and why the grade is what it is. The product
earns trust by showing the proof, so the writing does the same.
