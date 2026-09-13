import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

export const inject = ['skills']

export function apply(ctx) {
  const skillPath = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../skills/hulk-story/SKILL.md')
  const raw = fs.readFileSync(skillPath, 'utf8')
  const content = raw.replace(/^---\n[\s\S]*?\n---\n/, '')
  return ctx.skills.register({
    name: 'hulk-story',
    description: 'Open, write, continue, revise, inspect, or export a persistent novel with Hulk Story.',
    content,
    path: skillPath,
  })
}
