import { describe, expect, it, vi } from 'vitest'
import { generateRequestId } from '@/lib/requestId'

const uuidV4Pattern =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i

describe('generateRequestId', () => {
  it('returns a UUID v4 when crypto.randomUUID is unavailable', () => {
    const originalDescriptor = Object.getOwnPropertyDescriptor(globalThis, 'crypto')
    const testCrypto = {
      getRandomValues<T extends ArrayBufferView>(array: T): T {
        new Uint8Array(array.buffer, array.byteOffset, array.byteLength).fill(0xab)
        return array
      },
    } as unknown as Crypto
    Object.defineProperty(globalThis, 'crypto', { configurable: true, value: testCrypto })

    try {
      expect(generateRequestId()).toMatch(uuidV4Pattern)
    } finally {
      if (originalDescriptor) {
        Object.defineProperty(globalThis, 'crypto', originalDescriptor)
      } else {
        Reflect.deleteProperty(globalThis, 'crypto')
      }
    }
  })

  it('still returns a UUID v4 without Web Crypto', () => {
    const originalDescriptor = Object.getOwnPropertyDescriptor(globalThis, 'crypto')
    const random = vi.spyOn(Math, 'random').mockReturnValue(0.5)
    Object.defineProperty(globalThis, 'crypto', { configurable: true, value: undefined })

    try {
      expect(generateRequestId()).toMatch(uuidV4Pattern)
    } finally {
      random.mockRestore()
      if (originalDescriptor) {
        Object.defineProperty(globalThis, 'crypto', originalDescriptor)
      } else {
        Reflect.deleteProperty(globalThis, 'crypto')
      }
    }
  })
})
