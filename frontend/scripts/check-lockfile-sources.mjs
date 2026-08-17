import { readFileSync } from 'node:fs'

const lockfileUrl = new URL('../package-lock.json', import.meta.url)
const lockfile = JSON.parse(readFileSync(lockfileUrl, 'utf8'))
const allowedHost = 'registry.npmjs.org'
const sources = []

function collectResolvedSources(value, path = '$') {
  if (Array.isArray(value)) {
    value.forEach((item, index) => collectResolvedSources(item, `${path}[${index}]`))
    return
  }

  if (!value || typeof value !== 'object') {
    return
  }

  for (const [key, child] of Object.entries(value)) {
    const childPath = `${path}.${key}`
    if (key === 'resolved') {
      sources.push({ path: childPath, value: child })
    } else {
      collectResolvedSources(child, childPath)
    }
  }
}

collectResolvedSources(lockfile)

const rejected = sources.filter(({ value }) => {
  if (typeof value !== 'string') {
    return true
  }

  try {
    const url = new URL(value)
    return url.protocol !== 'https:' || url.hostname !== allowedHost
  } catch {
    return true
  }
})

if (rejected.length > 0) {
  console.error(`Rejected ${rejected.length} non-public dependency source(s):`)
  rejected.forEach(({ path, value }) => console.error(`- ${path}: ${String(value)}`))
  process.exit(1)
}

console.log(`Verified ${sources.length} dependency sources use https://${allowedHost}.`)
