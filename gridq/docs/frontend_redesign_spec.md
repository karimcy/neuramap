# GridQ Frontend Redesign Spec

**Status:** Approved brief, pending tooling setup before execution.
**Scope:** Frontend only. The MILP optimizer is owned by Olly (see `docs/module_ownership.md` and `docs/00_project_boundary.md`); do not modify it.
**Tooling prerequisite:** Requires Playwright MCP and Claude for Chrome connected before the benchmarking and refinement-loop sections can run. Tailwind styling can be authored directly or via the `artifact-design` skill / DesignSync.

---

## Goal and vision

Redesign the GridQ frontend into a clean, visual-first, guided experience that takes a non-expert user from "I have a site and some energy bills" to "I understand exactly what solar + battery system I should install and why," with minimal cognitive load at every step.

The current first pass fails on three fronts: it is too cluttered, too text-heavy, and it lets users attempt a run before the inputs needed for a complete report exist. The redesign must fix all three. The guiding principles:

- **Visual-first, not text-first.** Every screen should communicate through charts, maps, surfaces, and well-labelled controls rather than paragraphs. If something can be shown instead of written, show it. Aggressively cut explanatory copy.
- **Intuitive and guided.** The user should always know where they are, what is required of them right now, and what comes next. No screen should present more than one clear job to do.
- **Trustworthy through transparency.** The user should be able to watch their data being modelled as they enter it, and watch the engine work step by step when it runs. Nothing should feel like a black box.
- **Honest gating.** The product should never produce a partial or misleading report. If the inputs are incomplete, the run is blocked and the gap is made obvious, not hidden.
- **A standard to match.** GridQ.ai is the reference for quality. Our flow, our run experience, and our results presentation should look and feel at least as polished as theirs, benchmarked directly against their Tesco demo.

The end state is a tool a commercial energy buyer or installer could be handed cold and complete a full sizing report with confidence, and where the output (especially the sizing surface) looks like a serious engineering product rather than a prototype.

## Hard constraints

- **Do not touch the MILP optimization algorithm.** Olly owns it, including the known issue where it isn't selecting all BESS revenue mechanisms. This is frontend work only. Do not modify, refactor, or "fix" the optimizer.
- **No em-dashes anywhere in the UI copy.** Use commas, colons, or separate sentences. Em-dashes read as AI slop on the front end.
- **Gate the run.** The engine must not start unless every input required for a complete report is present and valid. Keep the run control disabled, and clearly show what is still missing, until the inputs are complete.
- **Far less on-screen text** than the current pass. Lead with visuals, not paragraphs.

## Navigation

- Persistent left **sidebar** with: **Dashboard**, **Projects**, **Equipment**.
- The Equipment page lets users **upload their own spec sheets** to extend the equipment catalogue.

## Front page

- More visuals demonstrating the product's appeal: sample results, surface plots, example dashboards. Minimal copy.

## Input wizard

Five stages, with a progress bar on every stage showing position in the pipeline and steps remaining.

1. **Site data** (all fields required to proceed). Two ways to set location:
   - Address field with **type-ahead autocomplete**: as the user types, show a dropdown of matching addresses to select from.
   - **Map with drop-a-pin**: placing a pin auto-fills the address field.
2. **Consumption data.** Input methods: manual entry, **CSV/Excel upload**, **bill scan** (PDF or image). Smart meter API is a future/stretch option, shown as "coming soon."
3. **Load profile.** Visualize the consumption data entered in stage 2.
4. **Equipment selection.** Choose solar panel, battery system, and inverter from a curated list of many options, each showing price (per Wp where applicable; use the correct unit per equipment type). Include an **"Auto-select" (recommended)** option. Do a thorough literature review to determine which real products belong on each list.
5. **Review.** Summarize every input and the auto-selected/chosen config with graphs and small visuals.

## Live validation while typing

- As values are entered, **plot the modelled data live** (curves) so users can immediately judge whether their inputs look sensible before running.

## Run experience

- Clicking run starts a **step-by-step progress bar** that transparently shows the exact pipeline stage in progress and **surfaces results as each stage completes**.
- Make it visually clean and appealing.

## Results visualization

- The **3D sizing surface** must be a **continuous surface**, not scattered points.
- Display it **side by side with the original** as a heat map, showing how the sizing was derived.

## Tooling

- **Claude for Chrome** (Chrome extension): for opening and driving GridQ.ai and inspecting our live site.
- **Playwright MCP**: for automated navigation, screenshotting across states and viewports, and verifying our UI renders cleanly.
- **Tailwind MCP**: for clean, aesthetically consistent styling, especially the run/progress and results views.

## Benchmarking step (do this first, before building)

1. Use **Claude for Chrome** to open **GridQ.ai** and run it end to end with the **Demo Tesco data**.
2. Document their flow: how inputs are collected, how the progress/run experience is presented, and how results are laid out (especially the sizing surface and any dashboards).
3. Capture reference screenshots of each meaningful screen. These become the quality target for the equivalent screens in our product.

## Iterative refinement loop (recursion until it meets the bar)

Treat the build as a loop, not a single pass. After each implementation increment, run this cycle and repeat until the exit criteria are met:

1. **Capture.** Use the Playwright MCP to drive our app through every screen and key state, capturing screenshots at desktop and mobile viewports. States to cover: empty/initial, partially filled (run still gated), fully valid (run enabled), mid-run (progress bar at several stages), and final results (including the continuous surface and its heat-map comparison).
2. **Compare.** Put our screenshots side by side with the GridQ.ai Tesco reference captured in the benchmarking step. Score each screen against the rubric below.
3. **Diagnose.** For every screen scoring below the bar, write down the specific gap: too much text, weak visual hierarchy, surface not continuous, progress bar not transparent, gating not enforced, em-dash present, layout broken at a viewport, and so on.
4. **Fix.** Apply targeted changes (using the Tailwind styling for refinements).
5. **Re-capture and re-score.** Repeat from step 1.

Continue looping until every screen passes, with a minimum of two full refinement passes even if the first looks acceptable. If a screen still fails after three passes, stop and surface it with the screenshots and the specific blocker rather than continuing to churn.

### Scoring rubric (each screen, pass/fail per item)

- **Clutter:** minimal text, one clear job per screen, strong visual hierarchy.
- **Comprehension:** a first-time user would know what to do without instruction.
- **Gating:** run is impossible while inputs are incomplete, and the missing items are obvious.
- **Transparency:** progress and intermediate results are visible during a run.
- **Data feedback:** live curves render correctly as inputs change.
- **Surface quality:** the 3D sizing surface is continuous and shown beside its heat-map comparison.
- **Polish:** spacing, typography, and color are consistent and at least on par with the GridQ.ai reference.
- **Responsiveness:** layout holds at desktop and mobile viewports.
- **Copy hygiene:** no em-dashes; concise, plain wording.

## Definition of done

- Run is impossible until all required inputs are valid, with the gap clearly shown otherwise.
- All 5 stages work with the specified input methods and a working progress bar.
- Live curves render during input; the 3D surface is continuous and shown beside its heat-map comparison.
- The run experience shows step-by-step progress and surfaces results as they complete.
- No em-dashes in any UI text; front page and results are visual-first.
- Every screen has been through at least two Playwright capture / compare / fix cycles and passes the rubric.
- Our flow compares favorably, screen by screen, to GridQ.ai's Tesco demo.
