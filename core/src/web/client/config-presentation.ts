/** Presentation only: never treat locally constructed defaults as a verified read.
 * Subsequent refreshes keep the last verified form and all unsaved input visible. */
export function configPresentation(root: HTMLElement, state: 'loading' | 'ready' | 'error'): void {
  root.setAttribute('data-config-state', state);
  root.setAttribute('aria-busy', String(state === 'loading'));
}
