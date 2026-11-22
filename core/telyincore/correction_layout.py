"""Render only host-generated replacements; preserve ordinary and quoted prose."""
import re

def render(parts, corrections):
    if not corrections: return ''.join(parts)
    kept = list(parts); seen = set(); replacements = {}
    # Only suppress a generated newline for an interior table-cell fragment.
    # Keep all original pipes and row endings; do not interpret quoted prose
    # or rewrite whole table rows with ambiguous replacement boundaries.
    cell_interior = set(); line_prefix = ''; opened_before = {}; active = set()
    for i, part in enumerate(parts):
        opened_before[i] = set(active)
        for marker in ('**','__'):
            if part.count(marker) % 2:
                if marker in active: active.remove(marker)
                else: active.add(marker)
        if line_prefix.lstrip().startswith('|') and '|' not in part and '\n' not in part:
            cell_interior.add(i)
        line_prefix = (line_prefix + part).rsplit('\n', 1)[-1]

    for index, value in sorted(corrections.items()):
        # Boundary checks may carry an unmatched Markdown opener from the
        # replaced heading. Strip that generated wrapper before deduplication;
        # the adjacent closing delimiter is handled below, not left dangling.
        value = re.sub(r'^(?:\*\*|__)+(?=〔Core)', '', value)
        note = value.strip()
        replacements[index] = (value if value.endswith('\n') else value + '\n') if note and note not in seen else ''
        if index in cell_interior: replacements[index] = replacements[index].rstrip('\n')
        seen.add(note)
        # Sentence splitting can leave a list prefix and the closing delimiter
        # of a replaced bold heading. Only remove adjacent syntax-only debris.
        if index and index-1 not in corrections and re.fullmatch(r'\s*(?:[-*+]|\d+[.)])\s*(?:\*\*|__)?\s*', kept[index-1]):
            kept[index-1] = ''
        pending = {m for m in ('**', '__') if parts[index].count(m) % 2}
        if index and index-1 not in corrections and opened_before[index]:
            # Close a heading whose opener was in an earlier sentence; the
            # host note itself must not inherit emphasis from that heading.
            kept[index-1] += ''.join(m for m in ('**','__') if m in opened_before[index])
            if replacements[index]: replacements[index] = '\n' + replacements[index]
            pending.symmetric_difference_update(opened_before[index])
        if index+1 < len(parts) and index+1 not in corrections:
            for _ in range(len(pending)):
                marker = next((m for m in pending if kept[index+1].startswith(m)), None)
                if marker is None: break
                kept[index+1] = kept[index+1][len(marker):]
                pending.remove(marker)
    text = ''.join(replacements.get(i, part) for i, part in enumerate(kept))
    return text.strip()
