# RFC: Viewport-Aware Workspace Dock Fallback

- Status: Draft
- Author: Noppanat Wadlom
- Created: 2026-07-12
- Target audience: Waypoint frontend maintainers and reviewers

## Summary

Make the workspace file explorer render as a full-screen sheet whenever its persisted preferred width exceeds the current browser layout viewport. Preserve the preference in local storage; do not shrink or overwrite it when the viewport narrows. When the viewport becomes wide enough again, return to the normal resizable left-side dock automatically.

## Motivation

The session page persists the workspace dock width in `waypoint.workspaceDockWidth`. The saved value is only clamped while a user drags or keyboard-resizes the dock. A subsequent desktop window resize can therefore leave a saved dock wider than the viewport. The dock is fixed to the left and has a fixed pixel width, so its header close control, pinned near the dock's right edge, becomes unreachable outside the viewport.

Phones already avoid this failure mode through the `max-width: 640px` full-screen-sheet rule. The same safe interaction model should apply on any client whose current viewport cannot contain the stored dock width, including a narrow desktop window.

## Goals

- Keep the file explorer's close control and content within the visible viewport after any window resize.
- Preserve the user's saved preferred dock width across temporary viewport changes.
- Reuse the existing mobile full-screen-sheet interaction model when a side dock does not fit.
- Restore the side dock and its prior width automatically when it fits again.
- Avoid applying desktop content-push padding while the explorer is shown as a sheet.

## Non-Goals

- Changing the minimum (300px) or drag-time maximum (60% of the current viewport) dock-width policy.
- Persisting a dock position. The dock is currently always fixed to the left; only width is persisted.
- Redesigning the explorer tree/preview responsive layout or the dedicated full-page explorer route.
- Changing the existing phone breakpoint or its visual design beyond sharing its sheet rules.

## Background / Current State

`SessionDetail` restores and writes the numeric dock-width preference. While open, it writes that value to `--wp-dock-width` on the document root. `WorkspaceFilesPanel` applies it through `.wp-dock { width: var(--wp-dock-width) }` and clamps it only for direct resize gestures. The existing mobile CSS makes `.wp-dock` width `100%`, removes the resize seam, and applies safe-area padding at `max-width: 640px`.

On a viewport wider than 640px but narrower than the saved preference, none of those mobile rules apply. For example, a saved 900px dock displayed in an 800px window remains 900px wide. The close button is positioned from the dock's right edge and lies beyond the 800px viewport.

Relevant implementation points:

- `frontend/src/components/SessionDetail.tsx` owns persisted dock width and root layout attributes.
- `frontend/src/components/WorkspaceFilesPanel.tsx` owns the dock element and resize interactions.
- `frontend/src/app/globals.css` defines the dock's wide, overlay, and mobile-sheet regimes.

## Requirements

### Functional Requirements

1. When the dock is open and its preferred width is greater than the current layout viewport width, Waypoint must render it as a full-screen sheet.
2. In sheet mode, the dock must occupy the visible viewport width, remove the side-dock resize seam, and retain the existing safe-area treatment for the header and bottom edge.
3. In sheet mode, the session layout must not reserve left-side body padding for the dock, even on a viewport that otherwise meets the wide-layout breakpoint.
4. The close button must remain visible and usable by pointer and keyboard in both dock and sheet modes. Sheet mode must also support Escape dismissal even when focus remains on the control that opened it or another underlying session control.
5. Resizing the browser window must update the mode without closing, remounting, or resetting the explorer.
6. Entering sheet mode must not write a reduced value to `waypoint.workspaceDockWidth`.
7. When the viewport grows so the preferred width fits again, the panel must return to side-dock mode at the saved width and re-enable resizing.
8. Existing phone behavior at `max-width: 640px` must remain a full-screen sheet regardless of the saved width.

### Non-Functional Requirements

- Determine fit against the browser layout viewport (`window.innerWidth`), which matches CSS width media-query behavior during desktop window resizing.
- Make the transition deterministic from the preferred width and current viewport; do not rely on a stale measurement of the dock DOM element.
- Preserve the current portal, focus, and mounted-state behavior so file-tree expansion and the opened file survive mode changes.
- Keep the implementation scoped to the workspace dock; do not add backend state or API changes.

## Proposed Design

Derive a transient `dockSheet` state from the saved preferred width and the current layout viewport. This is the single presentation-mode source of truth for both the existing phone sheet and the new overflow fallback:

```ts
const dockSheet = viewportWidth <= 640 || dockWidth > viewportWidth;
```

`SessionDetail` will track `window.innerWidth` on mount and on `window` `resize`. It will continue to own the persistent `dockWidth`, but will pass the derived mode to `WorkspaceFilesPanel` and expose a document-root attribute for global layout styling while the dock is open.

The dock panel will add a modifier class such as `wp-dock-sheet` when `dockSheet` is true. CSS will share the existing mobile sheet treatment between that modifier and the existing `max-width: 640px` rule: full viewport width, no right border or shadow, safe-area padding on all edges, and no resize seam. The desktop body-padding rule will apply only while the root reports an open dock that is not in sheet mode.

The preferred width remains unchanged in React state and local storage. Sheet width is a rendering override (`100%`), not a new persisted measurement. Since the explorer stays mounted in its existing portal, switching modes does not reload its data or reset local explorer state.

## Detailed Specification

### Mode resolution

1. Initialize a layout-viewport-width state from `window.innerWidth` after client mount.
2. Subscribe to `window.resize` while `SessionDetail` is mounted and update that state; unsubscribe during cleanup.
3. Compute `dockSheet` as `viewportWidth <= 640 || dockWidth > viewportWidth`. Equality remains side-dock mode above the phone breakpoint because a dock exactly as wide as the viewport still contains its right-inset close button.
4. Pass `dockSheet` to the panel and use it for the dock modifier class and the root mode attribute. CSS retains the `max-width: 640px` rule as a defensive presentation fallback, but JavaScript must classify that regime as a sheet too so its interactions are complete.

### Root layout state

While the workspace preview is open, set the current dock width custom property as today and set an explicit mode attribute when `dockSheet` is true. Use `useLayoutEffect` (or an equivalent pre-paint mechanism) for this root mutation, so the dock modifier class and body-padding selector change before the browser paints a resize transition. On every `dockSheet` transition, explicitly set or clear the mode attribute in that same pre-paint effect; remove both open and mode attributes during close/unmount cleanup.

The wide-screen body-padding selector must exclude sheet mode. This prevents a saved 1400px preference, viewed in a 1200px window, from both filling the screen as a sheet and adding 1400px of left body padding.

### Dock presentation

The dock modifier class must apply the same visual and interaction rules as the current phone sheet:

- `width: 100%`;
- no side border or side-dock shadow;
- safe-area padding on all four edges;
- hidden resize seam.

The existing container query continues to respond to the actual rendered dock width. A full-screen sheet with enough room may show tree and preview side by side; a narrower sheet keeps the existing Tree/Preview toggle behavior.

### Persistence, focus, and interactions

- Drag and keyboard resize continue to clamp the candidate saved width as they do today.
- In sheet mode, the resize separator is not exposed or focusable because it is not displayed.
- Update the dock-local Escape handler to close only when `event.key === "Escape"` and `event.defaultPrevented` is false, then call `event.preventDefault()` as it closes. Explorer controls can therefore consume Escape without closing the dock, and a dock-local close prevents the window handler from closing a second time. While an open `dockSheet` is active (including the phone regime), add a bubbling `keydown` handler on `window`, not `document`. For an Escape event that reaches it, close only when `event.defaultPrevented` is false; document-level modal handlers therefore receive and may consume Escape before the sheet evaluates it. Register the handler only for the open sheet and remove it during cleanup.
- Do not move focus merely because a non-modal side dock changes to a sheet. The window-level sheet handler makes Escape reliable even when the active element remains outside the portal; closing therefore leaves focus at the existing active element.
- The visible close button calls the existing close callback.
- No local-storage migration is necessary. Existing numeric values remain valid preferences.

## Approach Survey

### Option 1: Viewport-aware full-screen sheet (recommended)

Keep the stored preference and switch the rendered dock into the established full-screen-sheet model whenever it exceeds the viewport.

Advantages:

- Preserves a deliberate desktop width preference without surprising data loss.
- Guarantees reachable close and explorer controls.
- Reuses responsive behavior already implemented for phones.
- Returns to the preferred side-dock layout automatically after expanding the window.

Costs:

- Requires transient viewport tracking and a root mode attribute in addition to CSS.
- Requires care to keep the body-padding selector synchronized with sheet mode.

### Option 2: Clamp and persist the width on every window resize

On a viewport resize, reduce `dockWidth` to the current maximum and write the reduced value to local storage.

Advantages:

- Retains side-dock presentation and needs little additional CSS.

Disadvantages:

- Permanently discards a user's preferred width merely because the window was temporarily narrow.
- Produces a surprising side effect from browser-window resizing.
- Does not share the established mobile interaction model requested for constrained space.

### Option 3: CSS-only `min(100%, var(--wp-dock-width))`

Cap the rendered width at the viewport width while retaining the existing side-dock styling and layout state.

Advantages:

- Small CSS-only change that keeps the close control in view.

Disadvantages:

- A viewport-filling overlay would still present a side-dock border, shadow, and resize seam.
- The wide-layout body padding could still reserve the oversized preferred width.
- It lacks an explicit presentation mode, making future accessibility and layout behavior harder to reason about.

### Recommendation

Adopt Option 1. It solves the reachability defect without mutating the saved preference and makes constrained desktop windows behave consistently with mobile clients.

## Rollout / Migration Plan

1. Implement the transient viewport-width state and derived sheet mode in the session detail path.
2. Apply the dock modifier and root mode attribute while preserving existing open/close cleanup.
3. Refactor the mobile sheet CSS so the modifier shares the same rules without duplicating or diverging from phone behavior.
4. Manually test persisted wide widths across shrink and expand cycles before release.
5. Release without a storage migration or feature flag; the fallback is local, reversible on resize, and does not alter backend contracts.

## Validation Plan

### Automated checks

- Run `cd frontend && npm run lint`.
- Run `cd frontend && npm run build`.

### Manual browser scenarios

1. Save a wide dock width on a large desktop viewport, shrink the window below that width but above 640px, and confirm a full-screen sheet with a visible close control.
2. Expand the same window until the saved width fits and confirm the side dock returns at the original saved width.
3. While in sheet mode on a wide viewport, confirm no large left body-padding gap is introduced behind the session content.
4. Repeat the shrink/expand cycle with a file open and tree branches expanded; confirm explorer state survives.
5. With focus on the explorer, the original opener, and an underlying session control respectively, use Escape in sheet mode and confirm it closes the explorer. Confirm the explorer filter's Escape-to-clear behavior and an Escape event consumed by a higher-priority modal/control do not close the sheet.
6. Use the close button in both modes, then reopen the explorer.
7. Verify phone-sized viewports still use the existing full-screen sheet and have no visible resize seam.

## Risks and Mitigations

| Risk | Mitigation |
| --- | --- |
| Root layout and dock class disagree during an update | Derive both from the same `dockSheet` value in `SessionDetail` and update the root attributes with `useLayoutEffect` before paint. |
| Full-screen mode leaves wide-screen body padding active | Explicitly exclude sheet mode from the body-padding selector and include the 1200px-view/1400px-saved-width manual test. |
| Resize listeners leak or use stale state | Register one listener in an effect with cleanup; calculate mode from current React state rather than capturing width in a listener. |
| CSS for phones and constrained desktops diverges | Share a single sheet selector for both modes rather than copying declarations. |
| Browser chrome or mobile keyboard changes the visual viewport | Use the layout viewport for this width policy, consistent with existing CSS media queries; retain the independent phone breakpoint. |
| Sheet Escape conflicts with another control that owns Escape | Install the bubbling window handler only while the sheet is open and honor `event.defaultPrevented` before closing. |

## Security, Privacy, and Compliance

Not applicable. The change is presentation-only, uses the existing local width preference, and sends no new data.

## Operational Considerations

No backend, deployment, telemetry, or configuration changes are required. Browser console warnings or client-side exceptions during resize should be treated as regressions because the behavior is entirely frontend-local.

## Open Questions

None. The requested behavior and existing mobile implementation establish the product decision. Exact class and attribute names remain implementation details.

## Appendix

### Acceptance criteria

- A persisted dock wider than the current viewport never places its close control outside the viewport.
- The saved `waypoint.workspaceDockWidth` value is unchanged after entering or leaving sheet mode.
- A wide viewport still uses a resizable side dock and shifts the session layout only when the dock fits.
- Phone behavior remains unchanged.
