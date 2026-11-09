// Refresh src/shared/ from the IMS app (../frontend/src) when both live in the
// same repository. The console is a separate project so it can be built and
// hosted on its own; these few files (design tokens, UI kit, icons, API and
// money helpers) are copied, not imported, so the folder stands alone.
import { copyFileSync, existsSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const ims = join(root, '..', 'frontend', 'src')
if (!existsSync(ims)) {
  console.error(`No IMS app at ${ims}: nothing to sync (the console is standalone)`)
  process.exit(1)
}
const files = { 'components/ui.tsx': 'ui.tsx', 'components/Icon.tsx': 'Icon.tsx', 'lib/api.ts': 'api.ts', 'lib/money.ts': 'money.ts', 'index.css': 'index.css' }
for (const [from, to] of Object.entries(files)) {
  copyFileSync(join(ims, from), join(root, 'src', 'shared', to))
  console.log(`copied ${from} -> src/shared/${to}`)
}
// The UI kit imports the API helper from its IMS location; point it at the copy.
const ui = join(root, 'src', 'shared', 'ui.tsx')
writeFileSync(ui, readFileSync(ui, 'utf8').replaceAll("'../lib/api'", "'./api'").replaceAll("'../lib/money'", "'./money'"))
console.log('fixed imports in src/shared/ui.tsx')
