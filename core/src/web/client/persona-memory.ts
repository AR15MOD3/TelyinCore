import { corePhrase, coreSetUIText } from './ui-language';
/** Shared closing label for the two PERSONA & MEMORY pages. */
export function personaMemoryTail(): HTMLElement {
  const tail = document.createElement('footer');
  tail.className = 'persona-memory-tail page-footer';
  coreSetUIText(tail, 'textContent', corePhrase("人设与记忆"));
  return tail;
}
