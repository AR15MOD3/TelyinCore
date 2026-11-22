/** QQ connection is the layout authority. Reuse these unchanged structures for model plugins. */
export function infinityField(label: string, input: HTMLElement): HTMLElement {
  const box = document.createElement('label'); box.className = 'infinity-field';
  const name = document.createElement('span'); name.textContent = label;
  box.append(name, input); return box;
}
export function infinityToggle(label: string, input: HTMLInputElement): HTMLElement {
  const box = document.createElement('label'); box.className = 'infinity-toggle';
  const name = document.createElement('span'); name.textContent = label;
  box.append(input, name); return box;
}
export function infinityModelPicker(label: string, input: HTMLSelectElement,
  button: HTMLButtonElement): HTMLElement {
  const row = document.createElement('div'); row.className = 'infinity-model-picker';
  row.append(infinityField(label, input), button); return row;
}
export function infinityVoiceHeading(title: string, download: HTMLButtonElement): HTMLElement {
  const row = document.createElement('div'); row.className = 'infinity-voice-heading';
  const heading = document.createElement('h3'); heading.textContent = title;
  row.append(heading, download); return row;
}
