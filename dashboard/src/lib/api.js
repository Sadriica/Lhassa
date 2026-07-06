export async function requireOk(response, label = 'request') {
  if (response.ok) return response
  if (response.status === 404) {
    throw new Error(`${label} endpoint is missing. Restart the backend so the latest routes are loaded.`)
  }
  if (response.status >= 500) {
    throw new Error(`${label} backend error. Check the API logs or database path.`)
  }
  throw new Error(`${label} failed (${response.status}).`)
}
