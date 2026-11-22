import { mkdir, lstat, readdir, readFile, writeFile, open, rename, rmdir } from 'node:fs/promises';
import { join, resolve, dirname } from 'node:path';
import { createHash, randomUUID } from 'node:crypto';

const digest = bytes => createHash('sha256').update(bytes).digest('hex');
async function noLinks(path) {
  for (let part = resolve(path);; part = dirname(part)) {
    try { if ((await lstat(part)).isSymbolicLink()) throw new Error('Publication link refused'); }
    catch (error) { if (error.code !== 'ENOENT') throw error; }
    if (part === dirname(part)) break;
  }
}
async function persisted(path, bytes) {
  const file = await open(path, 'wx');
  try { await file.writeFile(bytes); await file.sync(); } finally { await file.close(); }
  if (!(await readFile(path)).equals(Buffer.from(bytes))) throw new Error('Publication readback mismatch');
}

export async function publishGeneration(output, staging) {
  const out = resolve(output), stage = resolve(staging);
  if (dirname(out) !== dirname(stage) || !stage.split(/[\\/]/).at(-1).startsWith('.web-stage-'))
    throw new Error('Staging boundary');
  await noLinks(out); await noLinks(stage);
  if (!(await lstat(stage)).isDirectory()) throw new Error('Staging directory');
  await mkdir(out, { recursive: true });
  const lock = join(out, '.publish-lock');
  await mkdir(lock); // Exclusive cooperating publisher; crash requires explicit recovery.
  let primary;
  try {
    const generation = randomUUID().replaceAll('-', '');
    const names = (await readdir(stage)).sort();
    if (!names.includes('index.html') || names.includes('manifest.json')) throw new Error('Stage inventory');
    for (const name of names) {
      const info = await lstat(join(stage, name));
      if (!/^[A-Za-z0-9][A-Za-z0-9._-]*$/.test(name) || !info.isFile() || info.isSymbolicLink() || info.size > 32 * 1024 * 1024)
        throw new Error('Stage file boundary');
    }
    // Pin document asset links to this immutable generation, not future current.
    let html = await readFile(join(stage, 'index.html'), 'utf8');
    html = html.replace(/(src|href)=(['"])\/([^'"?#]+)\2/g, (match, attr, quote, name) =>
      names.includes(name) ? `${attr}=${quote}/__web/${generation}/${name}${quote}` : match);
    await writeFile(join(stage, 'index.html'), html);
    const files = {};
    for (const name of names) {
      const bytes = await readFile(join(stage, name));
      const handle = await open(join(stage, name), 'r+');
      try { await handle.sync(); } finally { await handle.close(); }
      files[name] = { bytes: bytes.length, sha256: digest(bytes) };
    }
    const manifest = Buffer.from(JSON.stringify({ generation, files }));
    await persisted(join(stage, 'manifest.json'), manifest);
    const generations = join(out, 'generations');
    await noLinks(generations); await mkdir(generations, { recursive: true });
    await rename(stage, join(generations, generation));
    const pointer = join(out, `.current-${generation}.tmp`);
    await persisted(pointer, JSON.stringify({ generation, manifest_sha256: digest(manifest) }));
    await rename(pointer, join(out, 'current.json')); // Only visible switch; old pointer survives failure.
    return { generation, retained: true };
  } catch (error) { primary = error; throw error; }
  finally {
    try { await rmdir(lock); }
    catch (error) { if (primary) throw new AggregateError([primary, error], 'Publication and lock cleanup failed', { cause: primary }); throw error; }
  }
}
