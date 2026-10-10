import { useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { legacyTargets } from '../lib/routes'

/**
 * Compatibility shim: lets not-yet-migrated pages keep calling
 * `navigate('script-detail', { scriptId })` while the URL bar, back button
 * and refresh all work. Remove when ScriptDetail/RubricConfigs are migrated.
 */
export function useLegacyNavigate() {
  const navigate = useNavigate()
  return useCallback(
    (key, props = {}) => {
      const build = legacyTargets[key]
      navigate(build ? build(props) : '/')
    },
    [navigate],
  )
}
