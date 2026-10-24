/** Copy text using the secure Clipboard API, falling back for HTTP/local contexts. */
export async function copyTextToClipboard(text, options = {}) {
  if (typeof text !== 'string' || !text) {
    throw new Error('There is no text to copy.')
  }

  const clipboard = Object.hasOwn(options, 'clipboard')
    ? options.clipboard
    : globalThis.navigator?.clipboard
  const documentRef = Object.hasOwn(options, 'document')
    ? options.document
    : globalThis.document

  if (typeof clipboard?.writeText === 'function') {
    try {
      await clipboard.writeText(text)
      return
    } catch {
      // Clipboard permissions can reject even when the API exists; try the legacy path.
    }
  }

  if (!documentRef?.body || typeof documentRef.execCommand !== 'function') {
    throw new Error('Clipboard copy is unavailable.')
  }

  const textarea = documentRef.createElement('textarea')
  textarea.value = text
  textarea.setAttribute('readonly', '')
  textarea.setAttribute('aria-hidden', 'true')
  textarea.tabIndex = -1
  textarea.style.position = 'fixed'
  textarea.style.left = '-10000px'
  textarea.style.opacity = '0'

  documentRef.body.appendChild(textarea)
  let copied = false
  try {
    textarea.focus()
    textarea.select()
    copied = documentRef.execCommand('copy') === true
  } finally {
    textarea.remove()
  }

  if (!copied) {
    throw new Error('Clipboard copy is unavailable.')
  }
}
