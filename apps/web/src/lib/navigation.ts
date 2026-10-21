import type { LocationQueryValue, Router } from 'vue-router'

/** Only return to registered, authenticated application pages after sign-in. */
export function authReturnPath(
  router: Router,
  redirect: LocationQueryValue | LocationQueryValue[] | undefined,
): string {
  if (typeof redirect !== 'string' || !redirect.startsWith('/') || redirect.startsWith('//')) {
    return '/'
  }
  const destination = router.resolve(redirect)
  return destination.matched.some((record) => record.meta.requiresAuth)
    ? destination.fullPath
    : '/'
}
