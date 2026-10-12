# Mining app design references

## Selected direction

[Mode-centered cockpit](cockpit-astra-20261007/selected-direction.md): **07 Signal first** as the foundation, with **01 Flight deck** for the original game-view layout. Assist and Autopilot are the primary operating modes; Pause is a separate action/state.

The production dashboard implements this direction. [Implementation and validation](../architecture/mining-cockpit-ui-results.md) records the available controller capabilities, rendered checks, and remaining live verification.

## Current critique and proposed refinement

The [October 11 design critique](cockpit-critique-20261011/critique.md) reviews the current dashboard and proposes clearer task selection, actionable recovery, persistent execution controls, scoped findings, and direct capability labels. Its [interactive prototype](cockpit-critique-20261011/prototype.html) compares the game-view cockpit with a compact panel alongside the game using simulated states. [Rendered validation and limits](cockpit-critique-20261011/validation.md) separate the proposal from production and live-game evidence.

This is a reviewable refinement; it does not supersede the selected direction or change the production UI. The October 7 implementation record is historical: later changes added visible scanning and NPC rehearsal, so current capability claims should be checked against current source.

## Product directions (October 11, evening)

The [product-level critique](remote-control-proposals-20261011/critique.md) asks what the app should be, beyond fixes to the existing layout. Its [interactive mockups](remote-control-proposals-20261011/proposals.html) compare three directions in Running, Needs-you, and Game-not-found states: **A · Mission console**, **B · Companion dock**, and **C · Remote pocket**. **Selected 2026-10-11: [B + C together](remote-control-proposals-20261011/selected-direction.md).** The Companion dock is the in-game helper, and the Remote pocket is the remote control for scheduled farming. Both run on one shared core. The mockups are design proposals; production is not yet changed.

## Preserved proposals

All eight original Astra concept images are retained in Git under [cockpit-astra-20261007](cockpit-astra-20261007/README.md). Keep them as inspiration when real verification identifies a better arrangement; selecting a direction does not discard the alternatives.

- [Concept gallery](cockpit-astra-20261007/gallery.html) - all eight original proposals.
- [Astra comparison and initial design study](cockpit-astra-20261007/design-review.md) - useful qualities, tradeoffs, and state contracts.
- [Prototype validation](cockpit-astra-20261007/validation.md) - evidence already collected and its limits.
- [Earlier control-center study](mining-control-center-concept.md) - the preceding compact sidecar proposal.

Record future adjustments against the selected direction, identifying the observation that motivated the change and the concept that inspired it. Preserve the original artworks and dated studies rather than overwriting them.
