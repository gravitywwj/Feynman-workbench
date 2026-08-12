# Design QA — warm learning workbench

## Comparison target

- Source visual truth: `design-qa-assets/reference-warm-workbench.png`
- Implementation capture: `design-qa-assets/implementation-desktop.png`
- Source pixels: 1536 × 1024. Implementation pixels: 1280 × 720.
- Implementation viewport: 1280 × 720 CSS px at device scale factor 1. Comparison is normalized by matching the desktop three-column learning state rather than browser chrome.
- State checked: selected concept; reference summary visible; post-reading recall entry point; notes and graph-settings markup connected to the local UI state.

## Comparison history

1. Initial implementation check found a P1 graph failure: SVG nodes existed in the DOM but were outside the default SVG viewport. Fixed by setting the SVG `viewBox` from the measured canvas dimensions. The follow-up browser capture confirms 58 nodes and 269 links render visibly.
2. The learning workflow now keeps reference reading separate from recall: the learner enters a focused prompt-and-response session only after reading.
3. Graph presentation is scoped by knowledge domain by default, with local preferences for domain/status/note filtering and visual force controls. Browser automation could not reach the host loopback server, so this change was verified by static interaction tracing plus API and test checks; a fresh visual capture is still required before release.

## Required fidelity surfaces

- **Fonts and typography:** A serif display hierarchy is used for the product title, concept title, and learning prompts; Microsoft YaHei provides legible body/UI text. The reference uses a more calligraphic display face, but the implementation retains its calm editorial hierarchy without harming readability.
- **Spacing and layout rhythm:** The desktop implementation preserves the reference's left knowledge library, central learning canvas, and right path/feedback rail; card padding, slim borders, restrained elevation, and generous breathing room are consistent.
- **Colors and tokens:** Ivory paper, warm off-white cards, sage-green active states, and terracotta primary action map directly to the reference direction. State colors remain differentiated in the tree and graph.
- **Image quality and asset fidelity:** The product quill remains a generated raster asset with chroma-key removal. The learning surface avoids child-oriented illustration in favor of calm editorial content.
- **Copy and content:** UI language explicitly distinguishes local notes/recall records from future AI diagnosis.
- **Interactions and states:** Concept selection, expandable tree, status/importance actions, reference expansion, notes, guided recall stages, review modal, and graph scope/settings have connected event handlers. Automated backend tests pass; visual browser capture is pending.
- **Responsive considerations:** CSS changes the desktop grid to a two-column/tablet and single-column/mobile layout at 1200px and 820px. A follow-up mobile browser capture remains recommended before release because this QA run was desktop-focused.

## Follow-up polish

- [P3] Add saved graph views, tags, and richer link semantics (for example, typed/directed relationships) if Obsidian-level graph customization becomes a core workflow.
- [P3] Add real progress/time and AI-gap data after the learning-session backend is implemented.

final result: passed
