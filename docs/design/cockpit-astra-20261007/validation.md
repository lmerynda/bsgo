# Design prototype validation

Reviewed on 2026-10-07 using the T3 collaborative browser, the running mining dashboard, and local simulated states. No live game commands were sent.

## Verified

- Eight distinct original PNG files are saved, and Astra visually inspected all eight. All eight load in the concept gallery.
- The proxy returns the normalized `bsgo-observation-v1` read-only data. A test POST to its `/api/startup` returned HTTP 403 with `design_preview_controls_are_isolated`.
- Live image loading succeeds at native 2560 x 1440. CSS preserves `object-fit: contain`, opacity 1, and filter none. Freshness warnings are outside the image; no brightness control exists. No duplicate element IDs remain.
- At 1280 x 800, the page fits the viewport without document scrolling or routine-panel scrolling. Routine choices, scene checks, Start, Cancel, and Pause fit on screen. Initial compact layout measured an 863 x 447 image container; the final caption change measured 864 x 435, containing approximately 773 x 435 original 16:9 pixels. The prior cockpit reference contained approximately 720 x 405.
- At 1920 x 1080, the unified shelf measured an approximately 1441 x 651 image container, containing approximately 1157 x 651 native-aspect pixels. The page and routine panel had no overflow, and Start/Cancel were within the viewport. This geometry check preceded the small final caption/duplicate-ID cleanup.
- At 390 x 844, the page permits vertical scrolling and has no horizontal overflow. Elements within the main layout do not protrude past the viewport.
- In the ready demo, pointer interaction with all four labeled assessment checkboxes enabled **Simulate approach**. Pointer Start changed state to Approaching target, transferred simulated input ownership to Approach, disabled Start, and enabled Cancel. Pointer Cancel returned the simulation to paused with human ownership and explicit local confirmation.
- Assessment expiry after 30 seconds disabled Start; numeric zero remained a real `0 m/s` reading.
- The provided local scenario API exercised stale, rejected, cancellation-unverified, disconnected, and running states. Stale/disconnected values become Unknown. Cancellation-unverified remains visibly unresolved with Cancel available.
- Pointer Diagnostics opened a native dialog and focused its Close button; Escape dismissed it. The panel separates input request, delivery, and observed effect.
- Python preview-server syntax and inline JavaScript syntax were checked. Server stderr was empty at final inspection.

## Limits

The desktop geometry and workflow checks above concern this read-only design prototype. Production Start/Cancel/Pause wiring, real acknowledgment races, full history inspection, focus handover, accessibility/contrast auditing, zoom, long localized text, and live mining acceptance remain future implementation gates.

The scenario API checks are injected lifecycle checks; they complement the pointer flow and do not establish complete keyboard or human usability acceptance. Some browser automation operations failed intermittently. Saved gallery and an earlier demo screenshot exist in private browser artifacts, but attempts to save the final live prototype screenshot failed with `PreviewAutomationExecutionError`. No saved final live screenshot is claimed. Layout metrics, live loading, and pixel-preservation properties were inspected through browser evaluation.
