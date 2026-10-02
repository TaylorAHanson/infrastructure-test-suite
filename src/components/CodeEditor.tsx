import { forwardRef, useEffect, useImperativeHandle, useRef } from 'react'
import { basicSetup } from 'codemirror'
import { Annotation, Compartment, EditorSelection, EditorState } from '@codemirror/state'
import { EditorView, keymap } from '@codemirror/view'
import { indentWithTab } from '@codemirror/commands'
import { python, pythonLanguage } from '@codemirror/lang-python'
import { HighlightStyle, indentUnit, syntaxHighlighting } from '@codemirror/language'
import { snippetCompletion, type Completion, type CompletionContext, type CompletionResult } from '@codemirror/autocomplete'
import { lintGutter, setDiagnostics, type Diagnostic } from '@codemirror/lint'
import { unifiedMergeView } from '@codemirror/merge'
import { tags as t } from '@lezer/highlight'

import type { SdkIndex } from '../types'

export interface EditorDiagnostic {
  line: number
  message: string
  severity: 'error' | 'warning' | 'info'
}

export interface CodeEditorHandle {
  goToLine: (line: number) => void
  focus: () => void
  getValue: () => string
}

interface Props {
  value: string
  onChange?: (value: string) => void
  readOnly?: boolean
  diagnostics?: EditorDiagnostic[]
  sdk?: SdkIndex
  onSave?: () => void
  onRun?: () => void
  /** Review mode: show `value` as a unified diff against this text, with per-change accept/reject. */
  original?: string | null
  className?: string
  minHeight?: string
}

const External = Annotation.define<boolean>()

const rgb = (token: string) => `rgb(var(--brand-${token}))`

const theme = EditorView.theme({
  '&': { height: '100%', fontSize: '13px', backgroundColor: rgb('ink'), color: '#e6edf3' },
  '.cm-scroller': { fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace', lineHeight: '1.55' },
  '.cm-content': { caretColor: rgb('accent'), padding: '8px 0' },
  '.cm-cursor, .cm-dropCursor': { borderLeftColor: rgb('accent') },
  '&.cm-focused': { outline: 'none' },
  '&.cm-focused .cm-selectionBackground, .cm-selectionBackground, ::selection': {
    backgroundColor: 'rgb(var(--brand-info) / 0.25) !important',
  },
  '.cm-gutters': { backgroundColor: rgb('ink'), color: rgb('muted'), border: 'none', borderRight: `1px solid ${rgb('edge')}` },
  '.cm-activeLine': { backgroundColor: 'rgb(var(--brand-ink_soft) / 0.6)' },
  '.cm-activeLineGutter': { backgroundColor: 'rgb(var(--brand-ink_soft) / 0.6)', color: '#e6edf3' },
  '.cm-matchingBracket': { backgroundColor: 'rgb(var(--brand-info) / 0.2)', outline: 'none' },
  '.cm-tooltip': { backgroundColor: rgb('panel'), border: `1px solid ${rgb('edge')}`, color: '#e6edf3' },
  '.cm-tooltip-autocomplete > ul > li[aria-selected]': { backgroundColor: 'rgb(var(--brand-accent) / 0.2)', color: '#fff' },
  '.cm-completionDetail': { color: rgb('muted'), fontStyle: 'normal', marginLeft: '0.75em' },
  '.cm-completionInfo': { maxWidth: '420px', whiteSpace: 'pre-wrap', fontSize: '12px' },
  '.cm-panels': { backgroundColor: rgb('panel'), color: '#e6edf3' },
  '.cm-searchMatch': { backgroundColor: 'rgb(var(--brand-warn) / 0.3)' },
  '.cm-foldPlaceholder': { backgroundColor: rgb('ink_soft'), border: 'none', color: rgb('muted') },
  '.cm-changedLine': { backgroundColor: 'rgb(var(--brand-pass) / 0.10) !important' },
  '.cm-changedText': { background: 'rgb(var(--brand-pass) / 0.28) !important' },
  '.cm-deletedChunk': { backgroundColor: 'rgb(var(--brand-fail) / 0.12)' },
  '.cm-deletedChunk .cm-deletedText, .cm-deletedChunk del': { background: 'rgb(var(--brand-fail) / 0.28)', textDecoration: 'none' },
  '.cm-chunkButtons button': {
    border: `1px solid ${rgb('edge')}`, borderRadius: '4px', padding: '0 6px', marginLeft: '4px',
    background: rgb('ink_soft'), color: '#e6edf3', cursor: 'pointer', fontSize: '11px',
  },
  '.cm-chunkButtons button[name=accept]': { borderColor: rgb('pass'), color: rgb('pass') },
  '.cm-chunkButtons button[name=reject]': { borderColor: rgb('fail'), color: rgb('fail') },
  '.cm-lintRange-error': { backgroundImage: 'none', textDecoration: `underline wavy ${rgb('fail')}` },
  '.cm-lintRange-warning': { backgroundImage: 'none', textDecoration: `underline wavy ${rgb('warn')}` },
}, { dark: true })

const highlight = HighlightStyle.define([
  { tag: [t.keyword, t.controlKeyword, t.definitionKeyword, t.moduleKeyword, t.operatorKeyword], color: rgb('flaky') },
  { tag: [t.string, t.special(t.string)], color: rgb('pass') },
  { tag: [t.number, t.bool, t.null], color: rgb('warn') },
  { tag: t.comment, color: rgb('muted'), fontStyle: 'italic' },
  { tag: [t.function(t.definition(t.variableName)), t.function(t.variableName)], color: rgb('info') },
  { tag: t.propertyName, color: 'rgb(var(--brand-info) / 0.85)' },
  { tag: [t.className, t.definition(t.className)], color: rgb('warn') },
  { tag: [t.self, t.special(t.variableName)], color: rgb('accent') },
  { tag: [t.operator, t.punctuation, t.bracket], color: 'rgb(var(--brand-muted) / 0.95)' },
])

const GLOBALS: Completion[] = [
  snippetCompletion('with check("${claim}"):\n\t${}', {
    label: 'check', type: 'keyword', detail: 'with check(name)', boost: 10,
    info: 'One result. Passes when the block finishes; fails on assert or an API error.',
  }),
  snippetCompletion('with expect_deny("${claim}", at="${AUTHORIZE}"):\n\t${}', {
    label: 'expect_deny', type: 'keyword', detail: 'with expect_deny(name, at=…)', boost: 9,
    info: 'Counterfactual: passes only if the block is denied (AUTHORIZE, CONNECT or OPERATE).',
  }),
  snippetCompletion('sql("${SELECT 1}")', {
    label: 'sql', type: 'function', detail: 'sql(statement) → rows', boost: 8,
    info: 'Run a statement on a SQL warehouse; returns a list of row dicts.',
  }),
  snippetCompletion('http("${https://}")', {
    label: 'http', type: 'function', detail: 'http(url) → {status_code, headers, text}', boost: 7,
    info: 'Egress probe from the platform. Raises ConnectionFailed on DNS, network or TLS failure.',
  }),
  snippetCompletion('record("${key}", ${value})', { label: 'record', type: 'function', detail: 'record(key, value)', info: 'Attach evidence to the current check.' }),
  snippetCompletion('log(${})', { label: 'log', type: 'function', detail: 'log(*parts)', info: 'A run-log line.' }),
  { label: 'w', type: 'variable', detail: 'WorkspaceClient', info: 'Databricks SDK client, as the target identity. Keyword args only.' },
  { label: 'target', type: 'variable', detail: '{name, host, safety_class, tags, roles}' },
  { label: 'depth', type: 'variable', detail: '"quick" | "full"' },
  { label: 'params', type: 'variable', detail: 'per-run parameters' },
  ...['ApiError', 'PermissionDenied', 'NotFound', 'ConnectionFailed', 'SafetyBlocked', 'DepthSkipped'].map(
    (label) => ({ label, type: 'class' }) as Completion,
  ),
]

const RAW_API = ['get', 'post', 'put', 'patch', 'delete'].map(
  (m) => ({ label: m, type: 'method', detail: `w.api.${m}(path, …)` }) as Completion,
)

const VERB_RANK: Record<string, string> = { GET: 'read', POST: 'write', PUT: 'write', PATCH: 'write', DELETE: 'delete' }

function scriptCompletions(sdkRef: { current: SdkIndex | undefined }) {
  return (ctx: CompletionContext): CompletionResult | null => {
    const sdk = sdkRef.current
    const member = ctx.matchBefore(/\bw\.(\w+)\.(\w*)$/)
    if (member) {
      const [, svc, prefix] = /\bw\.(\w+)\.(\w*)$/.exec(member.text)!
      const from = member.to - prefix.length
      if (svc === 'api') return { from, options: RAW_API, validFor: /^\w*$/ }
      const methods = sdk?.[svc]
      if (!methods) return null
      return {
        from,
        validFor: /^\w*$/,
        options: Object.entries(methods).map(([name, m]) => ({
          label: name,
          type: 'method',
          detail: `${m.signature}  · ${VERB_RANK[m.verb] ?? m.verb}`,
          info: m.summary || undefined,
          boost: m.verb === 'GET' ? 1 : 0,
        })),
      }
    }
    const service = ctx.matchBefore(/\bw\.(\w*)$/)
    if (service) {
      const prefix = service.text.slice(2)
      return {
        from: service.to - prefix.length,
        validFor: /^\w*$/,
        options: [
          { label: 'api', type: 'namespace', detail: 'raw REST', boost: 2 },
          ...Object.entries(sdk ?? {}).map(([name, methods]) => ({
            label: name,
            type: 'namespace',
            detail: `${Object.keys(methods).length} methods`,
          })),
        ],
      }
    }
    const word = ctx.matchBefore(/\w+$/)
    if (!word && !ctx.explicit) return null
    const before = ctx.state.sliceDoc(Math.max(0, (word?.from ?? ctx.pos) - 1), word?.from ?? ctx.pos)
    if (before === '.') return null
    return { from: word?.from ?? ctx.pos, options: GLOBALS, validFor: /^\w*$/ }
  }
}

function toDiagnostics(state: EditorState, diags: EditorDiagnostic[]): Diagnostic[] {
  const lines = state.doc.lines
  return diags.map((d) => {
    const line = state.doc.line(Math.min(Math.max(1, d.line || 1), lines))
    const indent = line.text.length - line.text.trimStart().length
    return {
      from: line.from + indent,
      to: Math.max(line.from + indent, line.to),
      severity: d.severity,
      message: d.message,
    }
  })
}

const CodeEditor = forwardRef<CodeEditorHandle, Props>(function CodeEditor(
  { value, onChange, readOnly = false, diagnostics, sdk, onSave, onRun, original, className, minHeight },
  ref,
) {
  const host = useRef<HTMLDivElement>(null)
  const view = useRef<EditorView | null>(null)
  const callbacks = useRef({ onChange, onSave, onRun })
  callbacks.current = { onChange, onSave, onRun }
  const sdkRef = useRef(sdk)
  sdkRef.current = sdk
  const readOnlyC = useRef(new Compartment())
  const mergeC = useRef(new Compartment())

  useEffect(() => {
    if (!host.current) return
    const v = new EditorView({
      parent: host.current,
      state: EditorState.create({
        doc: value,
        extensions: [
          basicSetup,
          python(),
          pythonLanguage.data.of({ autocomplete: scriptCompletions(sdkRef) }),
          indentUnit.of('    '),
          EditorState.tabSize.of(4),
          keymap.of([
            indentWithTab,
            { key: 'Mod-s', preventDefault: true, run: () => (callbacks.current.onSave?.(), true) },
            { key: 'Mod-Enter', preventDefault: true, run: () => (callbacks.current.onRun?.(), true) },
          ]),
          theme,
          syntaxHighlighting(highlight),
          lintGutter(),
          readOnlyC.current.of([EditorState.readOnly.of(readOnly), EditorView.editable.of(!readOnly)]),
          mergeC.current.of([]),
          EditorView.updateListener.of((u) => {
            if (!u.docChanged) return
            if (u.transactions.some((tr) => tr.annotation(External))) return
            callbacks.current.onChange?.(u.state.doc.toString())
          }),
        ],
      }),
    })
    view.current = v
    return () => {
      v.destroy()
      view.current = null
    }
    // The view is created once; props flow in through the effects below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    const v = view.current
    if (!v) return
    const current = v.state.doc.toString()
    if (current !== value) {
      v.dispatch({
        changes: { from: 0, to: current.length, insert: value },
        selection: EditorSelection.cursor(Math.min(v.state.selection.main.head, value.length)),
        annotations: External.of(true),
      })
    }
  }, [value])

  useEffect(() => {
    view.current?.dispatch({
      effects: readOnlyC.current.reconfigure([EditorState.readOnly.of(readOnly), EditorView.editable.of(!readOnly)]),
    })
  }, [readOnly])

  useEffect(() => {
    view.current?.dispatch({
      effects: mergeC.current.reconfigure(
        original == null
          ? []
          : unifiedMergeView({ original, mergeControls: !readOnly, highlightChanges: true, gutter: true, syntaxHighlightDeletions: true }),
      ),
    })
  }, [original, readOnly])

  useEffect(() => {
    const v = view.current
    if (!v) return
    v.dispatch(setDiagnostics(v.state, toDiagnostics(v.state, diagnostics ?? [])))
  }, [diagnostics, value])

  useImperativeHandle(ref, () => ({
    goToLine: (line: number) => {
      const v = view.current
      if (!v) return
      const l = v.state.doc.line(Math.min(Math.max(1, line), v.state.doc.lines))
      v.dispatch({ selection: EditorSelection.cursor(l.from), effects: EditorView.scrollIntoView(l.from, { y: 'center' }) })
      v.focus()
    },
    focus: () => view.current?.focus(),
    getValue: () => view.current?.state.doc.toString() ?? value,
  }))

  return <div ref={host} className={`h-full min-h-0 overflow-hidden ${className ?? ''}`} style={{ minHeight }} />
})

export default CodeEditor
