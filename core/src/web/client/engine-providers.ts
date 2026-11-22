/** Billing mode is not wire authentication. Coding Plan keys remain API keys. */
import type { Preset } from './engine';

export function isPlanProvider(preset: Preset): boolean {
  return preset.auth === 'chatgpt' || /\/api\/coding\/paas\/v4\/?$/.test(preset.base_url);
}

export function providerChoices(rows: readonly Preset[], plan: boolean): Preset[] {
  const choices = rows.filter((row) => isPlanProvider(row) === plan);
  if (plan && !choices.some((row) => row.auth === 'chatgpt')) {
    const official = rows.find((row) => row.name === 'OpenAI');
    if (official) choices.unshift({ ...official, name: 'ChatGPT-Subscription', auth: 'chatgpt' });
  }
  return choices;
}

export function nextProviderName(base: string, names: readonly string[]): string {
  const taken = new Set(names);
  let name = base;
  for (let n = 2; taken.has(name); n++) name = `${base}-${n}`;
  return name;
}
