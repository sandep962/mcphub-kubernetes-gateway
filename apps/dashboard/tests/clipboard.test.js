import assert from 'node:assert/strict'
import test from 'node:test'
import { copyTextToClipboard } from '../src/clipboard.js'

function fallbackDocument({ copyResult = true } = {}) {
  let appended = null
  let removed = false
  const textarea = {
    value: '',
    style: {},
    tabIndex: 0,
    setAttribute() {},
    focus() {},
    select() {},
    remove() { removed = true },
  }
  return {
    body: { appendChild(node) { appended = node } },
    createElement(tag) {
      assert.equal(tag, 'textarea')
      return textarea
    },
    execCommand(command) {
      assert.equal(command, 'copy')
      assert.equal(appended.value, 'one-time-test-key')
      return copyResult
    },
    get appended() { return appended },
    get removed() { return removed },
  }
}

test('uses navigator clipboard API when it succeeds', async () => {
  let copied = ''
  const document = fallbackDocument()

  await copyTextToClipboard('one-time-test-key', {
    clipboard: { async writeText(value) { copied = value } },
    document,
  })

  assert.equal(copied, 'one-time-test-key')
  assert.equal(document.appended, null)
})

test('falls back to a temporary textarea when Clipboard API is unavailable', async () => {
  const document = fallbackDocument()

  await copyTextToClipboard('one-time-test-key', { clipboard: undefined, document })

  assert.equal(document.appended.value, 'one-time-test-key')
  assert.equal(document.removed, true)
})

test('falls back when Clipboard API rejects and reports failure if fallback fails', async () => {
  const document = fallbackDocument({ copyResult: false })

  await assert.rejects(
    copyTextToClipboard('one-time-test-key', {
      clipboard: { async writeText() { throw new Error('permission denied') } },
      document,
    }),
    /Clipboard copy is unavailable/,
  )
  assert.equal(document.removed, true)
})
