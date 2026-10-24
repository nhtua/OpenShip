export type ConnectorSubmissionOutcome = 'rejected' | 'uncertain'

export interface ConnectorErrorSummary {
  code: string | null
  message: string
  outcome: ConnectorSubmissionOutcome
  status: number | null
}

const safeMessages: Record<string, string> = {
  connector_origin_forbidden:
    'This browser origin is not allowed to configure connectors. Open OpenShip from its configured local address and try again.',
  global_management_forbidden:
    'This account cannot manage global connectors. Ask the configured installation operator to make this change.',
  connector_visibility_unsupported:
    'Brave and Tavily connectors must be configured globally by the installation operator.',
  invalid_connector_request:
    'The connector request was invalid. Check the form and try again.',
  credential_store_unavailable:
    'Connector credential storage is unavailable. Check the installation key configuration before trying again.',
  connector_not_found: 'The connector or request was not found.',
  connector_request_expired: 'The connector request has expired.',
}

const definiteRejections = new Set([
  'connector_origin_forbidden',
  'global_management_forbidden',
  'connector_visibility_unsupported',
  'invalid_connector_request',
])

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' ? value as Record<string, unknown> : null
}

function safeCode(value: unknown): string | null {
  const root = record(value)
  const response = record(root?.response)
  const responseData = record(response?.data)
  const directData = record(root?.data)
  const detail = record(responseData?.detail) ?? record(directData?.detail) ?? record(root?.detail)
  const candidates = [
    record(responseData?.error)?.code,
    record(detail?.error)?.code,
    record(directData?.error)?.code,
    record(record(root?.error)?.error)?.code,
    record(root?.error)?.code,
  ]
  return candidates.find((candidate): candidate is string =>
    typeof candidate === 'string' && Object.hasOwn(safeMessages, candidate),
  ) ?? null
}

function safeStatus(value: unknown): number | null {
  const root = record(value)
  const response = record(root?.response)
  const status = response?.status ?? root?.status
  return typeof status === 'number' && Number.isInteger(status) ? status : null
}

export function readConnectorError(error: unknown): ConnectorErrorSummary {
  const code = safeCode(error)
  const status = safeStatus(error)
  return {
    code,
    message: code
      ? safeMessages[code]
      : 'The connector request could not be confirmed. Check its status before entering another key.',
    outcome: code && definiteRejections.has(code) ? 'rejected' : 'uncertain',
    status,
  }
}
