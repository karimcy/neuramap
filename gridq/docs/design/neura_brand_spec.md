# Neura Energy brand spec for the site reports

Extracted from `Neura Energy Power-Provision Overview (1).pdf` (repo root, 2 pages, "COMPANY
OVERVIEW · JULY 2026"). **Every agent building or reviewing a report must match this.** Verify against
the PDF yourself with `Read` + `pages: "1-2"` if anything here is ambiguous.

**One deliberate divergence.** The source PDF uses em-dashes. The reports must contain **none**
(Olly's instruction). Match the palette, type and layout; do not copy the punctuation. Use a colon, a
comma, a full stop, or parentheses instead. This applies to rendered output, chart labels, axis
captions, footnotes and alt text.

## Palette

| Token | Hex | Used for |
|---|---|---|
| `--neura-orange` | `#E2551F` | logo mark, section labels, key series line, emphasis figures |
| `--neura-orange-mid` | `#EF8B4E` | secondary bars, "Recharge" band fill |
| `--neura-peach` | `#F5C9A8` | light fills, shaded bands, hatch strokes |
| `--neura-cream` | `#EAE7E1` | area fills under a demand curve, callout box background |
| `--neura-cream-light` | `#F5F3EF` | page-tint blocks, table zebra |
| `--neura-ink` | `#1F1F1D` | body text, primary chart line |
| `--neura-grey` | `#8A8A85` | axis labels, roman numerals, secondary caption |
| `--neura-rule` | `#D8D5CE` | hairline rules, table borders |
| `--neura-dashed` | `#9A9A94` | dashed reference lines ("Firm potential without Neura", "Minimum reserve") |

Hatching idiom: diagonal 45-degree orange-peach strokes on white to mark **unused / available
capacity**. This is the brand's signature figure device. Reuse it.

## Typography

- **Headline:** transitional serif, regular weight, sentence case, ends with a full stop.
  e.g. "Firm, near-term power, extracted from existing grid infrastructure."
  Stack: `Spectral, "Source Serif 4", "EB Garamond", Georgia, serif`.
- **Section label:** small caps, letterspaced ~0.12em, `--neura-orange`, sat in the LEFT MARGIN, with a
  grey roman numeral (I, II, III, IV) above it.
- **Body:** humanist sans, justified, `--neura-ink`, with a **bold lead-in clause** opening each
  paragraph. Stack: `"Source Sans 3", Inter, -apple-system, system-ui, sans-serif`.
- **Figure title:** small caps, letterspaced, `--neura-ink`, above the figure, with a hairline under it.
- **Footnotes:** tiny sans, superscript numerals, three columns, above the footer rule.
- **Header:** right-aligned small caps, grey, then `PRIVATE & CONFIDENTIAL` in orange beneath.
- **Footer:** hairline rule, then `NEURA ENERGY` left and `PAGE n OF m` right, small letterspaced caps.

## Logo

Orange rounded square containing a white angular glyph, followed by the "Neura Energy" serif wordmark.
Reproduce as **inline SVG** (the artifact/report must be self-contained: no external assets, no remote
fonts, and font stacks must degrade gracefully to system fonts).

## The figure the client asked to replicate

Page 2, titled **"HOW THE BATTERY FIRMS THE CONNECTION"**. This is the grid-augmentation diagram. Two
stacked panels sharing a 00:00 to 24:00 x-axis:

**Top panel, y = "Grid connection capacity", 0 to 100%:**
- A black/ink line: available import capacity across the day, dipping hard around 17:00 to 20:00.
- Warm cream area fill beneath it, labelled "From the grid".
- A solid `--neura-orange` block filling the dip between the two reference lines, labelled
  "From the battery". This block is the whole point of the figure: the battery is filling a shortfall.
- A solid orange horizontal reference line at 78 labelled "Firm potential with Neura (78%)".
- A dashed grey line at 30 labelled "Firm potential without Neura (30%)".
- Peach "Recharge" bands at the shoulders (early morning, late evening).

**Bottom panel, y = "Battery SOC", 0 to 100%:**
- An orange line: charging up through the night, plateau, steep discharge during the event, partial
  recovery after.
- A dashed grey "Minimum reserve" line at ~25%.

For a real site this must be **driven by the run record**: the actual half-hourly connection headroom,
the actual solved dispatch and the actual SoE trajectory. It is a real exhibit, not an illustration.
No number in it may be authored.

## A second reusable idiom, from page 1

**"THE SHAPE OF THE OPPORTUNITY"**: two side-by-side load-duration curves, cream fill under the curve,
hatched region above labelled "Available capacity", with a callout naming the percentage of hours the
battery imports. A per-site load-duration curve is directly derivable from the real HH series and is a
strong figure for these reports.

## Style rules for the prose

- **No em-dashes anywhere.** Also avoid the AI-prose tells: no "delve", "leverage", "robust",
  "it's worth noting", "in today's landscape", no tricolon padding, no sentence that only restates the
  previous one.
- Short declarative sentences. Lead with the finding, then the evidence.
- Bold lead-in clause per paragraph, as the PDF does.
- Concise and impactful: the source fits a whole company thesis on two pages. Match that density.
- Figure-heavy: prefer an exhibit to a paragraph wherever a number can be shown.
- **Never author a number.** Every figure comes from the deterministic run record. Prose states what the
  record says and nothing more.
