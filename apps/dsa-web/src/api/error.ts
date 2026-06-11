import axios from 'axios';

export type ApiErrorCategory =
  | 'agent_disabled'
  | 'missing_params'
  | 'llm_not_configured'
  | 'model_tool_incompatible'
  | 'invalid_tool_call'
  | 'portfolio_oversell'
  | 'portfolio_busy'
  | 'upstream_llm_400'
  | 'upstream_timeout'
  | 'upstream_network'
  | 'local_connection_failed'
  | 'http_error'
  | 'unknown';

export interface ParsedApiError {
  title: string;
  message: string;
  rawMessage: string;
  status?: number;
  category: ApiErrorCategory;
}

type ResponseLike = {
  status?: number;
  data?: unknown;
  statusText?: string;
};

type ErrorCarrier = {
  response?: ResponseLike;
  code?: string;
  message?: string;
  parsedError?: ParsedApiError;
  cause?: unknown;
};

type CreateParsedApiErrorOptions = {
  title: string;
  message: string;
  rawMessage?: string;
  status?: number;
  category?: ApiErrorCategory;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

function pickString(...values: unknown[]): string | null {
  for (const value of values) {
    if (typeof value === 'string' && value.trim()) {
      return value.trim();
    }
  }
  return null;
}

function stringifyValue(value: unknown): string | null {
  if (value === null || value === undefined) {
    return null;
  }

  if (typeof value === 'string') {
    return value.trim() || null;
  }

  if (typeof value === 'number' || typeof value === 'boolean') {
    return String(value);
  }

  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function getResponse(error: unknown): ResponseLike | undefined {
  if (!isRecord(error)) {
    return undefined;
  }

  const response = (error as ErrorCarrier).response;
  return response && typeof response === 'object' ? response : undefined;
}

function getErrorCode(error: unknown): string | undefined {
  return isRecord(error) && typeof (error as ErrorCarrier).code === 'string'
    ? (error as ErrorCarrier).code
    : undefined;
}

function getErrorMessage(error: unknown): string | null {
  if (typeof error === 'string') {
    return error.trim() || null;
  }

  if (error instanceof Error && error.message.trim()) {
    return error.message.trim();
  }

  if (isRecord(error) && typeof (error as ErrorCarrier).message === 'string') {
    const message = (error as ErrorCarrier).message?.trim();
    return message || null;
  }

  return null;
}

function getCauseMessage(error: unknown): string | null {
  if (!isRecord(error)) {
    return null;
  }

  return getErrorMessage((error as ErrorCarrier).cause);
}

function buildMatchText(parts: Array<string | undefined | null>): string {
  return parts
    .filter((part): part is string => typeof part === 'string' && part.trim().length > 0)
    .join(' | ')
    .toLowerCase();
}

function includesAny(haystack: string, needles: string[]): boolean {
  return needles.some((needle) => haystack.includes(needle.toLowerCase()));
}

function hideNonEnglishText(value: string): string {
  // Show untranslated backend errors rather than hiding them \u2014 a masked
  // error is undiagnosable. The prefix marks it as raw backend output.
  return /[\u3400-\u9fff]/.test(value)
    ? `Backend error (untranslated): ${value}`
    : value;
}

function extractValidationDetail(detail: unknown): string | null {
  if (!Array.isArray(detail)) {
    return null;
  }

  const parts = detail
    .map((item) => {
      if (!isRecord(item)) {
        return stringifyValue(item);
      }

      const location = Array.isArray(item.loc)
        ? item.loc.map((segment) => String(segment)).join('.')
        : null;
      const message = pickString(item.msg, item.message, item.error);
      if (!location && !message) {
        return stringifyValue(item);
      }
      return [location, message].filter(Boolean).join(': ');
    })
    .filter((entry): entry is string => Boolean(entry));

  return parts.length > 0 ? parts.join('; ') : null;
}

function extractErrorCode(data: unknown): string | null {
  if (!isRecord(data)) {
    return null;
  }

  const detail = data.detail;
  if (isRecord(detail)) {
    return pickString(detail.error, detail.code, data.error, data.code);
  }

  return pickString(data.error, data.code);
}

export function extractErrorPayloadText(data: unknown): string | null {
  if (typeof data === 'string') {
    return data.trim() || null;
  }

  if (Array.isArray(data)) {
    return extractValidationDetail(data) ?? stringifyValue(data);
  }

  if (!isRecord(data)) {
    return stringifyValue(data);
  }

  const detail = data.detail;
  if (isRecord(detail)) {
    return (
      pickString(detail.message, detail.error)
      ?? extractValidationDetail(detail.detail)
      ?? stringifyValue(detail)
    );
  }

  return (
    pickString(
      detail,
      data.message,
      data.error,
      data.title,
      data.reason,
      data.description,
      data.msg,
    )
    ?? extractValidationDetail(detail)
    ?? stringifyValue(data)
  );
}

export function createParsedApiError(options: CreateParsedApiErrorOptions): ParsedApiError {
  const message = hideNonEnglishText(options.message);
  const rawMessage = hideNonEnglishText(options.rawMessage?.trim() || options.message);
  return {
    title: hideNonEnglishText(options.title),
    message,
    rawMessage,
    status: options.status,
    category: options.category ?? 'unknown',
  };
}

export function isParsedApiError(value: unknown): value is ParsedApiError {
  return isRecord(value)
    && typeof value.title === 'string'
    && typeof value.message === 'string'
    && typeof value.rawMessage === 'string'
    && typeof value.category === 'string';
}

export function isApiRequestError(
  value: unknown,
): value is Error & ErrorCarrier & { parsedError: ParsedApiError } {
  return value instanceof Error
    && isRecord(value)
    && isParsedApiError((value as ErrorCarrier).parsedError);
}

export function formatParsedApiError(parsed: ParsedApiError): string {
  if (!parsed.title.trim()) {
    return parsed.message;
  }
  if (parsed.title === parsed.message) {
    return parsed.title;
  }
  return `${parsed.title}: ${parsed.message}`;
}

export function getParsedApiError(error: unknown): ParsedApiError {
  if (isParsedApiError(error)) {
    return error;
  }
  if (isRecord(error) && isParsedApiError((error as ErrorCarrier).parsedError)) {
    return (error as ErrorCarrier).parsedError as ParsedApiError;
  }
  return parseApiError(error);
}

export function createApiError(
  parsed: ParsedApiError,
  extra: { response?: ResponseLike; code?: string; cause?: unknown } = {},
): Error & ErrorCarrier & { status?: number; category: ApiErrorCategory; rawMessage: string } {
  const apiError = new Error(formatParsedApiError(parsed)) as Error & ErrorCarrier & {
    status?: number;
    category: ApiErrorCategory;
    rawMessage: string;
  };
  apiError.name = 'ApiRequestError';
  apiError.parsedError = parsed;
  apiError.response = extra.response;
  apiError.code = extra.code;
  apiError.status = parsed.status;
  apiError.category = parsed.category;
  apiError.rawMessage = parsed.rawMessage;
  if (extra.cause !== undefined) {
    apiError.cause = extra.cause;
  }
  return apiError;
}

export function attachParsedApiError(error: unknown): ParsedApiError {
  const parsed = parseApiError(error);
  if (isRecord(error)) {
    const carrier = error as ErrorCarrier;
    carrier.parsedError = parsed;
  }
  if (error instanceof Error) {
    error.name = 'ApiRequestError';
    error.message = formatParsedApiError(parsed);
  }
  return parsed;
}

export function isLocalConnectionFailure(error: unknown): boolean {
  return parseApiError(error).category === 'local_connection_failed';
}

export function parseApiError(error: unknown): ParsedApiError {
  const response = getResponse(error);
  const status = response?.status;
  const payloadText = extractErrorPayloadText(response?.data);
  const errorCode = extractErrorCode(response?.data);
  const errorMessage = getErrorMessage(error);
  const causeMessage = getCauseMessage(error);
  const code = getErrorCode(error);
  const rawMessage = pickString(payloadText, response?.statusText, errorMessage, causeMessage, code)
    ?? 'The request did not complete. Try again in a moment.';
  const matchText = buildMatchText([rawMessage, errorMessage, causeMessage, code, errorCode, response?.statusText]);

  if (includesAny(matchText, ['agent mode is not enabled', 'agent_mode'])) {
    return createParsedApiError({
      title: 'Agent Mode Is Off',
      message: 'This feature requires Agent mode. Enable it and try again.',
      rawMessage,
      status,
      category: 'agent_disabled',
    });
  }

  const hasStockCodeField = includesAny(matchText, ['stock_code', 'stock_codes']);
  const hasMissingParamText = includesAny(matchText, ['必须提供 stock_code 或 stock_codes', 'missing', 'required']);
  if (hasStockCodeField && hasMissingParamText) {
    return createParsedApiError({
      title: 'Missing Required Input',
      message: 'Add the stock code or required input and try again.',
      rawMessage,
      status,
      category: 'missing_params',
    });
  }

  if (errorCode === 'portfolio_oversell' || includesAny(matchText, ['oversell detected'])) {
    return createParsedApiError({
      title: 'Sell Quantity Exceeds Available Position',
      message: 'The sell quantity is larger than the available position. Fix the trade entry and try again.',
      rawMessage,
      status,
      category: 'portfolio_oversell',
    });
  }

  if (errorCode === 'portfolio_busy' || includesAny(matchText, ['portfolio ledger is busy'])) {
    return createParsedApiError({
      title: 'Portfolio Ledger Is Busy',
      message: 'The portfolio ledger is processing another change. Try again shortly.',
      rawMessage,
      status,
      category: 'portfolio_busy',
    });
  }

  const noConfiguredLlm = (
    includesAny(matchText, ['all llm models failed']) && includesAny(matchText, ['last error: none'])
  ) || includesAny(matchText, [
    'no llm configured',
    'litellm_model not configured',
    'ai analysis will be unavailable',
  ]);
  if (noConfiguredLlm) {
    return createParsedApiError({
      title: 'No LLM Model Configured',
      message: 'Configure a primary model, channel, or API key in Settings and try again.',
      rawMessage,
      status,
      category: 'llm_not_configured',
    });
  }

  if (includesAny(matchText, [
    'tool call',
    'function call',
    'does not support tools',
    'tools is not supported',
    'reasoning',
  ])) {
    return createParsedApiError({
      title: 'Model Does Not Support Tool Calls',
      message: 'This model is not suitable for Agent/tool-call workflows. Switch to a tool-capable model.',
      rawMessage,
      status,
      category: 'model_tool_incompatible',
    });
  }

  if (includesAny(matchText, [
    'thought_signature',
    'missing function',
    'missing tool',
    'invalid tool call',
    'invalid function call',
  ])) {
    return createParsedApiError({
      title: 'Invalid Tool-Call Response',
      message: 'The upstream model returned an invalid tool-call structure. Switch models or disable incompatible reasoning settings.',
      rawMessage,
      status,
      category: 'invalid_tool_call',
    });
  }

  if (includesAny(matchText, ['timeout', 'timed out', 'read timeout', 'connect timeout']) || code === 'ECONNABORTED') {
    return createParsedApiError({
      title: 'Upstream Request Timed Out',
      message: 'The backend timed out while reaching an external dependency. Try again or check network/proxy settings.',
      rawMessage,
      status,
      category: 'upstream_timeout',
    });
  }

  if (
    status === 502
    || status === 503
    || includesAny(matchText, [
      'dns',
      'enotfound',
      'name or service not known',
      'temporary failure in name resolution',
      'proxy',
      'tunnel',
      '502',
      '503',
    ])
  ) {
    return createParsedApiError({
      title: 'Backend Cannot Reach External Services',
      message: 'The local backend is reachable, but it cannot access a model or data provider. Check proxy, DNS, or outbound network settings.',
      rawMessage,
      status,
      category: 'upstream_network',
    });
  }

  const hasLlmProviderHint = includesAny(matchText, [
    'chat/completions',
    'generativelanguage',
    'openai',
    'gemini',
  ]);
  if (status === 400 && hasLlmProviderHint) {
    return createParsedApiError({
      title: 'Upstream Model Rejected The Request',
      message: 'The backend is running, but the model provider rejected the request. Check model name, parameters, or tool-call compatibility.',
      rawMessage,
      status,
      category: 'upstream_llm_400',
    });
  }

  const localConnectionFailed = !response && (
    includesAny(matchText, ['fetch failed', 'failed to fetch', 'network error', 'connection refused', 'econnrefused'])
    || code === 'ERR_NETWORK'
    || code === 'ECONNREFUSED'
  );
  if (localConnectionFailed) {
    return createParsedApiError({
      title: 'Cannot Connect To Local Backend',
      message: 'The browser cannot reach the local backend. Make sure it is running on the expected host and port.',
      rawMessage,
      status,
      category: 'local_connection_failed',
    });
  }

  if (payloadText || status) {
    return createParsedApiError({
      title: 'Request Failed',
      message: payloadText ?? `The request did not complete (HTTP ${status}).`,
      rawMessage,
      status,
      category: 'http_error',
    });
  }

  return createParsedApiError({
    title: 'Request Failed',
    message: rawMessage,
    rawMessage,
    status,
    category: 'unknown',
  });
}

export function toApiErrorMessage(error: unknown, fallback = 'The request did not complete. Try again in a moment.'): string {
  const parsed = getParsedApiError(error);
  const message = formatParsedApiError(parsed);
  return message.trim() || fallback;
}

export function isAxiosApiError(error: unknown): boolean {
  return axios.isAxiosError(error);
}
