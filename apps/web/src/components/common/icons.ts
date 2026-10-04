/**
 * Typed PrimeIcons name tuple (plan Task 2.6).
 * Every name is verified to exist in the installed primeicons CSS
 * (see tests/UIPrimitives.test.ts > iconsUseOnlyInstalledGlyphs).
 * primeicons 8.0.2 does NOT define `pi-anchor`; use the OS typographic mark.
 */
export const PRIME_ICON_NAMES = [
  'comments',
  'plus',
  'user',
  'lock',
  'envelope',
  'sign-out',
  'bars',
  'chevron-left',
  'chevron-right',
  'times',
  'sun',
  'moon',
  'spinner',
  'info-circle',
  'arrow-down',
  'send',
] as const

export type PrimeIconName = (typeof PRIME_ICON_NAMES)[number]
