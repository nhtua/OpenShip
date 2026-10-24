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

export function connectorStatusDotClass(status: ConnectorCardStatus): string {
  switch (status) {
    case 'configured': return 'bg-emerald-500'
    case 'not-configured':
    case 'disabled': return 'bg-muted-foreground'
    case 'needs-attention': return 'bg-destructive'
  }
}

export function connectorStatusChipClass(status: ConnectorCardStatus): string {
  switch (status) {
    case 'configured': return 'border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300'
    case 'not-configured':
    case 'disabled': return 'border-border bg-muted text-muted-foreground'
    case 'needs-attention': return 'border-destructive/40 bg-destructive/10 text-destructive'
  }
}

export const connectorCatalog: readonly ConnectorCatalogEntry[] = [
  { kind: 'brave', name: 'Brave Search', publisher: 'OpenShip', visibility: 'global', initials: 'B' },
  { kind: 'tavily', name: 'Tavily Search', publisher: 'OpenShip', visibility: 'global', initials: 'T' },
]

export function connectorStatusPresentation(connector?: Pick<ConnectorUsage, 'status'>): ConnectorStatusPresentation {
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
