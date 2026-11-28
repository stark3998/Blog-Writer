// Lists the license of every installed package (from package-lock.json) and
// fails when one is not on the permissive allowlist. `--prod` limits the check
// to what ships in the editor bundle.
import { readFileSync } from 'node:fs'


const ALLOWED = new Set(['MIT', 'ISC', 'BSD-2-Clause', 'BSD-3-Clause', 'Apache-2.0', '0BSD', 'BlueOak-1.0.0', 'Python-2.0', 'CC0-1.0'])
const prodOnly = process.argv.includes('--prod')
const lock = JSON.parse(readFileSync(new URL('../package-lock.json', import.meta.url), 'utf8'))

const rows = []
for (const [path, info] of Object.entries(lock.packages)) {
  if (path === '' || (prodOnly && info.dev)) {
    continue
  }
  const name = path.replace(/^.*node_modules\//, '')
  // package-lock v3 records each package's license; optional platform packages may not be installed.
  const license = typeof info.license === 'string' ? info.license : JSON.stringify(info.license ?? 'UNKNOWN')
  // SPDX: one allowed choice is enough for OR, but AND needs every part (conservative for mixed expressions).
  const parts = license.split(/\s+(?:OR|AND)\s+|[()]/).map(part => part.trim()).filter(Boolean)
  const ok = /\sAND\s/.test(license) ? parts.every(part => ALLOWED.has(part)) : parts.some(part => ALLOWED.has(part))
  rows.push({ name, version: info.version, license, dev: Boolean(info.dev), ok })
}

rows.sort((a, b) => a.name.localeCompare(b.name))
const byLicense = Object.groupBy(rows, row => row.license)
for (const [license, packages] of Object.entries(byLicense).sort()) {
  console.log(`${license}: ${packages.length}`)
}
const bad = rows.filter(row => !row.ok)
for (const row of bad) {
  console.log(`NOT ALLOWED: ${row.name}@${row.version} (${row.license})${row.dev ? ' [dev]' : ''}`)
}
console.log(`${rows.length} packages checked${prodOnly ? ' (production only)' : ''}; ${bad.length} outside the allowlist`)
process.exitCode = bad.length > 0 ? 1 : 0
