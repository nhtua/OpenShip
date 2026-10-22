import type { ConnectorKind, ConnectorUsage } from '@/types'

export interface ConnectorCatalogEntry {
  kind: ConnectorKind
  name: string
  publisher: 'OpenShip'
  visibility: 'global'
  initials: string
}

export type ConnectorCardStatus = 'configured' | 'not-configured' | 'disabled' | 'needs-attention'

export interface ConnectorStatusPresentation {
  status: ConnectorCardStatus
  label: string
  detail: string | null
}

export const connectorCatalog: readonly ConnectorCatalogEntry[] = [
  { kind: 'brave', name: 'Brave Search', publisher: 'OpenShip', visibility: 'global', initials: 'B' },
  { kind: 'tavily', name: 'Tavily Search', publisher: 'OpenShip', visibility: 'global', initials: 'T' },
]

export function connectorStatusPresentation(connector?: ConnectorUsage): ConnectorStatusPresentation {
  if (!connector) {
    return { status: 'not-configured', label: 'Not configured', detail: null }
  }

  switch (connector.status) {
    case 'configured':
      return { status: 'configured', label: 'Configured', detail: null }
    case 'disabled':
      return { status: 'disabled', label: 'Disabled', detail: null }
    case 'expired':
    case 'revoked':
    case 'unavailable':
      return { status: 'needs-attention', label: 'Needs attention', detail: connector.status }
  }
}
