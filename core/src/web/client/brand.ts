/** Folded T + detached core. Theme-neutral geometry, no external assets. */
export const CORE_MARK_T = 'M4 5H28L22 11H18V25L12 29V11H4Z';
export const CORE_MARK_CORE = 'M24 17L29 22L24 27L19 22Z';

export function coreBrandMark(): SVGSVGElement {
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('viewBox', '0 0 32 32');
  svg.setAttribute('aria-hidden', 'true');
  svg.setAttribute('focusable', 'false');
  svg.setAttribute('class', 'rail-mark core-brand-icon');
  for (const [shape, opacity] of [[CORE_MARK_T, '1'], [CORE_MARK_CORE, '.48']]) {
    const path = document.createElementNS(ns, 'path');
    path.setAttribute('d', shape);
    path.setAttribute('fill', 'currentColor');
    path.setAttribute('opacity', opacity);
    svg.append(path);
  }
  return svg;
}
