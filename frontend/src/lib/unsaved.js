/**
 * Tiny "unsaved changes" flag. Editors set it while dirty; navigation links ask
 * before leaving. (BrowserRouter has no route blocker, and this keeps it simple.)
 * Reloading the tab is covered separately by a beforeunload handler in the editor.
 */
let dirty = false

export const setUnsaved = (value) => { dirty = value }

export const confirmLeave = () =>
  !dirty || window.confirm('You have unsaved changes. Leave without saving?')

/** onClick handler for links: cancels navigation unless the user confirms. */
export const guardNavigation = (event) => {
  if (!confirmLeave()) event.preventDefault()
  else dirty = false
}
