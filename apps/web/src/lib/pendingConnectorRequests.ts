/** In-memory request IDs only; secret values never enter this module. */
export const pendingConnectorRequests = new Map<string, string>()
