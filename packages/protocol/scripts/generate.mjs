#!/usr/bin/env node
/**
 * 由 JSON Schema 生成 TypeScript 类型与 Python Pydantic 模型。
 *
 * 事实源：schema/events.v1.json
 * 输出：src/generated/types.ts、python/copilot_protocol/models.py
 *
 * 生成器版本固定，保证同一 schema 在任何机器上产出一致。
 */
import { execFileSync } from 'node:child_process'
import { mkdirSync, writeFileSync } from 'node:fs'
import { dirname, join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'
import { compileFromFile } from 'json-schema-to-typescript'

const GENERATOR_VERSION = '0.99.0'

const here = dirname(fileURLToPath(import.meta.url))
const pkgRoot = join(here, '..')
const repoRoot = join(pkgRoot, '..', '..')
const schemaPath = join(pkgRoot, 'schema', 'events.v1.json')
const tsOut = join(pkgRoot, 'src', 'generated', 'types.ts')
const pyOut = join(pkgRoot, 'python', 'copilot_protocol', 'models.py')

const BANNER = `/* eslint-disable */
/**
 * AUTO-GENERATED from ${relative(repoRoot, schemaPath).split('\\').join('/')}
 * 由 packages/protocol/scripts/generate.mjs 生成，请勿手改。
 * 变更协议请改 schema 后运行：pnpm -C packages/protocol generate
 */`

const tsSource = await compileFromFile(schemaPath, {
  bannerComment: BANNER,
  style: { semi: false, singleQuote: true },
  additionalProperties: false,
  declareExternallyReferenced: true,
  format: true,
})

mkdirSync(dirname(tsOut), { recursive: true })
writeFileSync(tsOut, tsSource, 'utf8')
console.log(`generated ${relative(repoRoot, tsOut)}`)

mkdirSync(dirname(pyOut), { recursive: true })
execFileSync(
  'uvx',
  [
    '--from',
    `datamodel-code-generator==${GENERATOR_VERSION}`,
    'datamodel-codegen',
    '--input',
    schemaPath,
    '--input-file-type',
    'jsonschema',
    '--output',
    pyOut,
    '--class-name',
    'EventV1',
    '--output-model-type',
    'pydantic_v2.BaseModel',
    '--target-python-version',
    '3.11',
    '--snake-case-field',
    '--allow-population-by-field-name',
    '--use-standard-collections',
    '--use-annotated',
    '--enum-field-as-literal',
    'all',
    '--disable-timestamp',
  ],
  { stdio: 'inherit', cwd: repoRoot },
)
console.log(`generated ${relative(repoRoot, pyOut)}`)
