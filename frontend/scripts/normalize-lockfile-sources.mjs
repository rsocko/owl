import { readFileSync, writeFileSync } from 'node:fs'

const lockfileUrl = new URL('../package-lock.json', import.meta.url)
const lockfile = JSON.parse(readFileSync(lockfileUrl, 'utf8'))
const publicRegistry = 'https://registry.npmjs.org/'
const registryPathMarker = '/npm/registry/'
let normalized = 0

for (const entry of Object.values(lockfile.packages ?? {})) {
  if (!entry.resolved) {
    continue
  }

  const source = new URL(entry.resolved)
  if (source.hostname === 'registry.npmjs.org' && source.protocol === 'https:') {
    continue
  }

  const markerIndex = source.pathname.indexOf(registryPathMarker)
  if (markerIndex === -1) {
    throw new Error(`Cannot normalize non-registry dependency source: ${entry.resolved}`)
  }

  entry.resolved = new URL(
    source.pathname.slice(markerIndex + registryPathMarker.length),
    publicRegistry,
  ).href
  normalized += 1
}

writeFileSync(lockfileUrl, `${JSON.stringify(lockfile, null, 2)}\n`)
console.log(`Normalized ${normalized} dependency sources to ${publicRegistry}.`)
