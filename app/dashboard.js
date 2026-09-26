(() => {
  'use strict';

  const state = {
    phase: 'initial',
    sourceMode: 'live',
    forecastMode: 'live',
    horizonBars: 75,
    exchange: 'NSE',
    currentResult: null,
    previousResult: null,
    pending: null,
    requestSequence: 0,
    forecastController: null,
    explanationController: null,
    cachedExplanation: null,
    explanationVisible: false,
    lastAttempt: null,
    selectedListing: null,
    searchResults: [],
    searchSequence: 0,
    searchController: null,
    searchTimer: null,
    activeSearchIndex: -1,
    chartMode: 'candles',
    profile: null,
    authFirstRun: false,
  };

  const elements = {};
  const byId = (id) => document.getElementById(id);

  function cacheElements() {
    [
      'source-live', 'source-csv', 'mode-live', 'mode-validation', 'exchange-control', 'exchange-nse', 'exchange-bse',
      'horizon-24', 'horizon-75', 'horizon-120', 'local-note',
      'theme-light', 'theme-dark', 'theme-status',
      'auth-screen', 'auth-title', 'auth-form', 'auth-name-label', 'auth-name', 'auth-email', 'auth-error', 'auth-submit',
      'dashboard-view', 'account-controls', 'account-name', 'profile-open', 'sign-out-button',
      'profile-dialog', 'profile-form', 'profile-close', 'profile-cancel', 'profile-name', 'profile-email',
      'profile-exchange', 'profile-chart-mode', 'profile-currency', 'profile-horizon', 'profile-mode',
      'profile-volume', 'profile-error',
      'live-form', 'csv-form', 'ticker-input', 'csv-input', 'live-forecast-button',
      'symbol-search-spinner', 'symbol-search-list', 'selected-symbol-help',
      'csv-forecast-button', 'quick-tickers', 'request-status', 'request-status-text',
      'readiness-indicator', 'readiness-text', 'error-panel', 'error-title', 'error-message',
      'empty-state', 'empty-title', 'forecast-result', 'previous-result-label', 'result-symbol',
      'result-exchange', 'result-source', 'direction-summary', 'direction-icon', 'direction-label',
      'yahoo-meta',
      'movement-value', 'last-close', 'final-close', 'chart-symbol', 'chart-interval',
      'chart-eyebrow', 'chart-candles', 'chart-line', 'legend-observed', 'legend-forecast', 'legend-actual-item',
      'chart-container', 'chart-loading', 'chart-loading-title', 'forecast-chart', 'chart-summary',
      'forecast-range', 'forecast-horizon', 'forecast-horizon-detail', 'record-counts',
      'chart-context', 'chart-context-detail', 'chart-observed-window', 'chart-forecast-window',
      'forecast-time', 'forecast-date', 'model-observed', 'model-forecast', 'model-name',
      'model-copy', 'model-runtime', 'model-cache',
      'validation-panel', 'validation-mae', 'validation-rmse', 'validation-final-error', 'validation-direction',
      'explanation-badge', 'explanation-content', 'explanation-text', 'explain-button',
      'explanation-status', 'retry-button', 'switch-exchange-button', 'error-csv-button',
    ].forEach((id) => { elements[id] = byId(id); });
  }

  function selectedValue(name) {
    return document.querySelector(`input[name="${name}"]:checked`)?.value || '';
  }

  function selectedHorizon() {
    return Number(selectedValue('forecast-horizon') || state.horizonBars || 75);
  }

  function selectedMode() {
    return selectedValue('forecast-mode') || state.forecastMode || 'live';
  }

  function cssVar(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  function setTheme(theme, manual = false) {
    document.documentElement.dataset.theme = theme;
    document.documentElement.style.colorScheme = theme;
    document.querySelector('meta[name="theme-color"]')?.setAttribute('content', theme === 'dark' ? '#151b1c' : '#f5f6f5');
    elements['theme-light']?.setAttribute('aria-pressed', String(theme === 'light'));
    elements['theme-dark']?.setAttribute('aria-pressed', String(theme === 'dark'));
    if (elements['theme-status']) elements['theme-status'].textContent = `${theme === 'dark' ? 'Dark' : 'Light'} appearance`;
    if (manual) localStorage.setItem('kronos-theme', theme);
    if (state.currentResult && !elements['forecast-result'].hidden) drawChart(state.currentResult);
  }

  function setButtonLabel(button, text) {
    button.querySelector('.button-label').textContent = text;
  }

  function sanitizeTicker(value) {
    return value.trim().toUpperCase();
  }

  function normalizeAttempt(ticker, exchange) {
    const cleanTicker = sanitizeTicker(ticker);
    if (!cleanTicker) return '';
    if (cleanTicker.startsWith('^') || cleanTicker.endsWith('.NS') || cleanTicker.endsWith('.BO')) {
      return cleanTicker;
    }
    return `${cleanTicker}.${exchange === 'BSE' ? 'BO' : 'NS'}`;
  }

  function isSymbolLike(value) {
    return /^[A-Z0-9.^=\-]{1,20}(\.(NS|BO))?$/i.test(value.trim());
  }

  function usefulSearchLength(value) {
    return value.trim().replace(/[^a-z0-9]/gi, '').length;
  }

  function isIndianMarket(result) {
    return result?.currency === 'INR' || result?.exchange === 'NSE' || result?.exchange === 'BSE';
  }

  function formatPrice(value, result = state.currentResult) {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) return '-';
    const formatted = new Intl.NumberFormat('en-IN', {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }).format(numeric);
    if (!isIndianMarket(result)) return formatted;
    return state.profile?.preferences?.currency_display === 'code' ? `INR ${formatted}` : `₹${formatted}`;
  }

  function formatAxisPrice(value) {
    return new Intl.NumberFormat('en-IN', {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }).format(Number(value));
  }

  function formatVolume(value) {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) return '0';
    if (Math.abs(numeric) >= 10000000) return `${(numeric / 10000000).toFixed(1)}Cr`;
    if (Math.abs(numeric) >= 100000) return `${(numeric / 100000).toFixed(1)}L`;
    if (Math.abs(numeric) >= 1000) return `${(numeric / 1000).toFixed(1)}K`;
    return String(Math.round(numeric));
  }

  function formatMovement(value) {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) return '0.00%';
    return `${numeric > 0 ? '+' : ''}${numeric.toFixed(2)}%`;
  }

  function formatDateTimeIST(timestamp, options = {}) {
    const value = new Date(timestamp);
    if (Number.isNaN(value.getTime())) return '-';
    const includeYear = options.year !== false;
    return `${value.toLocaleString('en-IN', {
      timeZone: 'Asia/Kolkata',
      day: 'numeric',
      month: 'short',
      year: includeYear ? 'numeric' : undefined,
      hour: 'numeric',
      minute: '2-digit',
      hour12: true,
    })} IST`;
  }

  function addMinutes(timestamp, minutes) {
    const value = new Date(timestamp);
    if (Number.isNaN(value.getTime())) return '';
    return new Date(value.getTime() + minutes * 60 * 1000).toISOString();
  }

  function formatTime(timestamp) {
    const value = new Date(timestamp);
    if (Number.isNaN(value.getTime())) return { time: '-', date: 'Local time' };
    return {
      time: value.toLocaleTimeString('en-IN', { timeZone: 'Asia/Kolkata', hour: '2-digit', minute: '2-digit' }),
      date: `${value.toLocaleDateString('en-IN', { timeZone: 'Asia/Kolkata', day: 'numeric', month: 'short', year: 'numeric' })} IST`,
    };
  }

  function sameMarketDate(start, end) {
    const first = new Date(start);
    const second = new Date(end);
    if (Number.isNaN(first.getTime()) || Number.isNaN(second.getTime())) return false;
    return first.toLocaleDateString('en-CA', { timeZone: 'Asia/Kolkata' }) === second.toLocaleDateString('en-CA', { timeZone: 'Asia/Kolkata' });
  }

  function formatTimeOnlyIST(timestamp) {
    const value = new Date(timestamp);
    if (Number.isNaN(value.getTime())) return '-';
    return value.toLocaleTimeString('en-IN', { timeZone: 'Asia/Kolkata', hour: 'numeric', minute: '2-digit', hour12: true }).toUpperCase();
  }

  function formatDateOnlyIST(timestamp) {
    const value = new Date(timestamp);
    if (Number.isNaN(value.getTime())) return '-';
    return value.toLocaleDateString('en-IN', { timeZone: 'Asia/Kolkata', day: 'numeric', month: 'short' });
  }

  function formatTimeRangeIST(start, end) {
    const startLabel = formatTimeOnlyIST(start);
    const endLabel = formatTimeOnlyIST(end);
    const startMeridiem = startLabel.endsWith('AM') ? 'AM' : startLabel.endsWith('PM') ? 'PM' : '';
    const endMeridiem = endLabel.endsWith('AM') ? 'AM' : endLabel.endsWith('PM') ? 'PM' : '';
    return startMeridiem && startMeridiem === endMeridiem
      ? `${startLabel.replace(` ${startMeridiem}`, '')}–${endLabel}`
      : `${startLabel}–${endLabel}`;
  }

  function forecastSessionParts(result, timing) {
    const forecast = (result.chart?.forecast || []).filter((point) => point.timestamp);
    if (!forecast.length && timing.first_forecast_timestamp && timing.final_forecast_timestamp) {
      forecast.push({ timestamp: timing.first_forecast_timestamp }, { timestamp: timing.final_forecast_timestamp });
    }
    const sessions = [];
    let current = [];
    forecast.forEach((point, index) => {
      const previous = forecast[index - 1];
      const gap = previous ? (new Date(point.timestamp) - new Date(previous.timestamp)) / 60000 : 5;
      if (current.length && (gap > 5 || !sameMarketDate(point.timestamp, current[0].timestamp))) {
        sessions.push(current);
        current = [];
      }
      current.push(point);
    });
    if (current.length) sessions.push(current);

    const labels = sessions.map((session) => {
      const start = session[0].timestamp;
      const end = addMinutes(session[session.length - 1].timestamp, 5);
      return `${formatDateOnlyIST(start)}: ${formatTimeRangeIST(start, end)} IST`;
    });
    return {
      labels,
      closedText: sessions.length > 1 ? 'Market closed' : '',
      text: labels.join(' · Market closed · '),
    };
  }

  function formatWindow(label, start, end) {
    if (!start || !end) return `${label}: -`;
    const displayEnd = label === 'Forecast' ? addMinutes(end, 5) : end;
    if (sameMarketDate(start, end)) {
      const date = new Date(start).toLocaleDateString('en-IN', { timeZone: 'Asia/Kolkata', day: 'numeric', month: 'short', year: 'numeric' });
      return `${label}: ${date}, ${formatTimeOnlyIST(start)}–${formatTimeOnlyIST(displayEnd)} IST`;
    }
    return `${label}: ${formatDateTimeIST(start)}–${formatDateTimeIST(displayEnd)}`;
  }

  function formatMarketMeta(result, timing) {
    const latestTimestamp = timing.observed_end || timing.last_yahoo_market_bar;
    const latest = latestTimestamp ? formatDateTimeIST(latestTimestamp, { year: false }) : 'Unavailable';
    const source = result.data_source_name || result.input_source || 'Source unavailable';
    const rows = [
      ['Source', source],
      ['Latest observed bar', latest],
    ];
    if (result.source_type === 'live') {
      const retrieved = timing.yahoo_retrieved_at
        ? formatDateTimeIST(timing.yahoo_retrieved_at, { year: false })
        : 'Unavailable';
      rows.push(['Retrieved', retrieved]);
    }
    rows.push(['Data status', result.market_freshness || 'Freshness unavailable']);
    return rows;
  }

  function renderMarketMeta(result, timing) {
    elements['yahoo-meta'].replaceChildren(...formatMarketMeta(result, timing).map(([label, value]) => {
      const item = document.createElement('span');
      const labelNode = document.createElement('b');
      labelNode.textContent = `${label}: `;
      item.append(labelNode, value);
      return item;
    }));
  }

  function finalForecastBarText(timing) {
    if (!timing.final_forecast_timestamp) return '';
    return `Final forecast bar: ${formatTimeOnlyIST(timing.final_forecast_timestamp)}–${formatTimeOnlyIST(addMinutes(timing.final_forecast_timestamp, 5))} IST`;
  }

  function setReadiness(tone, text) {
    elements['readiness-indicator'].dataset.tone = tone;
    elements['readiness-text'].textContent = text;
  }

  function setRequestStatus(tone, text) {
    elements['request-status'].dataset.tone = tone;
    elements['request-status-text'].textContent = text;
  }

  function resultDirection(result) {
    const movement = Number(result.forecast_pct_change);
    if (Math.abs(movement) < 0.005) return 'neutral';
    return movement > 0 ? 'up' : 'down';
  }

  function resultFromPayload(payload) {
    const direction = resultDirection(payload);
    return {
      ...payload,
      direction,
      display_symbol: payload.display_symbol || payload.normalized_symbol || payload.input_source || 'Forecast',
      normalized_symbol: payload.normalized_symbol || payload.display_symbol || payload.input_source || 'Forecast',
      exchange: payload.exchange || (payload.source_type === 'csv' ? 'CSV' : 'Market'),
      chart: payload.chart || { observed: [], forecast: [], actual: [] },
    };
  }

  function renderResult(result, previous = false) {
    const direction = result.direction;
    const icon = direction === 'up' ? '↑' : direction === 'down' ? '↓' : '→';
    const directionText = direction === 'up' ? 'Forecast direction · upward' : direction === 'down' ? 'Forecast direction · downward' : 'Forecast direction · neutral';
    const created = formatTime(result.forecast_created_at);

    elements['empty-state'].hidden = true;
    elements['forecast-result'].hidden = false;
    elements['previous-result-label'].hidden = !previous;
    elements['previous-result-label'].textContent = previous ? `Previous forecast: ${result.normalized_symbol}` : '';

    elements['result-symbol'].textContent = result.normalized_symbol;
    elements['result-exchange'].textContent = result.exchange || (result.source_type === 'csv' ? 'CSV' : 'Market');
    elements['result-source'].textContent = result.company_name
      ? `${result.company_name} · ${result.data_source_name || result.input_source}`
      : result.data_source_name || result.input_source;
    const timing = result.timing || {};
    renderMarketMeta(result, timing);
    elements['direction-summary'].dataset.direction = direction;
    elements['direction-icon'].textContent = icon;
    elements['direction-label'].textContent = directionText;
    elements['movement-value'].textContent = formatMovement(result.forecast_pct_change);
    elements['last-close'].textContent = formatPrice(result.last_observed_close, result);
    elements['final-close'].textContent = formatPrice(result.forecast_final_close, result);
    elements['chart-symbol'].textContent = result.normalized_symbol;
    elements['chart-interval'].textContent = result.interval_label || 'Each point = 5 minutes';
    elements['forecast-range'].textContent = `${formatPrice(result.forecast_min_close, result)} to ${formatPrice(result.forecast_max_close, result)}`;
    elements['chart-observed-window'].textContent = formatWindow('Observed', timing.observed_start, timing.observed_end);
    const forecastSessions = forecastSessionParts(result, timing);
    elements['chart-forecast-window'].textContent = `Forecast: ${forecastSessions.text} · Trading-time view · Overnight closures are omitted`;
    elements['forecast-horizon'].textContent = result.forecast_horizon_label || `Next ${result.forecast_rows} market bars`;
    elements['forecast-horizon-detail'].textContent = result.forecast_horizon_detail || `${result.forecast_rows} predicted five-minute market bars`;
    elements['record-counts'].textContent = `${result.input_rows} input bars`;
    elements['chart-context'].textContent = `Latest ${timing.displayed_historical_count || result.chart.observed.length} bars`;
    elements['chart-context-detail'].textContent = `Displayed from the ${timing.total_model_input_count || result.input_rows} bars used by Kronos`;
    elements['forecast-time'].textContent = created.time;
    elements['forecast-date'].textContent = created.date;
    elements['model-observed'].textContent = `${result.input_rows} input bars`;
    elements['model-forecast'].textContent = `${result.forecast_rows} forecast bars`;
    elements['model-name'].textContent = result.model;
    elements['model-runtime'].textContent = Number.isFinite(Number(result.inference_time_seconds))
      ? `${Number(result.inference_time_seconds).toFixed(1)}s`
      : '-';
    elements['model-cache'].textContent = result.cache_hit ? 'Cached forecast' : 'Fresh run';
    elements['model-copy'].textContent = `Kronos reads ${result.input_rows} past five-minute bars and predicts ${result.forecast_rows} future five-minute bars.`;

    const validation = result.validation;
    elements['validation-panel'].hidden = !validation;
    elements['legend-actual-item'].hidden = !validation;
    if (validation) {
      elements['validation-mae'].textContent = formatPrice(validation.mae, result);
      elements['validation-rmse'].textContent = formatPrice(validation.rmse, result);
      elements['validation-final-error'].textContent = validation.final_error_pct == null ? '-' : `${Number(validation.final_error_pct).toFixed(2)}%`;
      elements['validation-direction'].textContent = validation.directional_match ? 'Match' : 'Miss';
    }

    const movementPhrase = Number(result.forecast_pct_change) >= 0
      ? `up ${Math.abs(Number(result.forecast_pct_change)).toFixed(2)} percent`
      : `down ${Math.abs(Number(result.forecast_pct_change)).toFixed(2)} percent`;
    const validationPhrase = validation ? ` Historical performance compares this forecast with ${validation.compared_rows} observed bars from the future window. MAE is ${formatPrice(validation.mae, result)} and RMSE is ${formatPrice(validation.rmse, result)}.` : '';
    elements['chart-summary'].textContent = `${result.normalized_symbol}: observed data ends at ${formatDateTimeIST(timing.observed_end)}. Kronos prediction starts at ${formatDateTimeIST(timing.first_forecast_timestamp)}. The predicted candles and dashed line are model-generated. Closed-market periods omitted: ${forecastSessions.closedText || 'none in the displayed prediction'}. Forecast sessions: ${forecastSessions.text}. Final forecast bar: ${formatTimeRangeIST(timing.final_forecast_timestamp, addMinutes(timing.final_forecast_timestamp, 5))} IST. The last observed close is ${formatPrice(result.last_observed_close, result)} and the final forecast close is ${formatPrice(result.forecast_final_close, result)}, ${movementPhrase}.${validationPhrase}`;
    elements['forecast-chart'].title = `Predicted close: ${formatPrice(result.forecast_final_close, result)} at the final forecast bar.`;
    drawChart(result);
  }

  function setChartMode(mode) {
    state.chartMode = mode === 'line' ? 'line' : 'candles';
    elements['chart-candles']?.setAttribute('aria-pressed', String(state.chartMode === 'candles'));
    elements['chart-line']?.setAttribute('aria-pressed', String(state.chartMode === 'line'));
    if (elements['chart-eyebrow']) {
      elements['chart-eyebrow'].textContent = state.chartMode === 'candles'
        ? 'Observed and predicted candles'
        : 'Observed and predicted close';
    }
    if (elements['legend-observed']) {
      elements['legend-observed'].textContent = state.chartMode === 'candles' ? 'Observed candles' : 'Observed close';
    }
    if (elements['legend-forecast']) {
      elements['legend-forecast'].textContent = state.chartMode === 'candles' ? 'Kronos predicted candles' : 'Kronos predicted close';
    }
    if (state.currentResult && !elements['forecast-result'].hidden) drawChart(state.currentResult);
  }

  function renderLoading(attempt) {
    state.phase = 'loading';
    setReadiness('loading', 'Forecasting');
    setRequestStatus('loading', `Running Kronos-base locally for ${attempt.label}. This can take up to a minute.`);
    elements['error-panel'].hidden = true;
    elements['chart-loading-title'].textContent = `Running Kronos-base locally for ${attempt.label}`;

    if (state.currentResult) {
      renderResult(state.currentResult, true);
      elements['chart-loading'].hidden = false;
    } else {
      elements['empty-state'].hidden = false;
      elements['empty-title'].textContent = `Running Kronos-base locally for ${attempt.label}`;
      elements['forecast-result'].hidden = true;
    }
    resetExplanation(`Explanation unavailable while ${attempt.label} is being forecast.`);
    updateForecastButtons();
  }

  function renderError(error, attempt) {
    state.phase = 'error';
    state.pending = null;
    setReadiness('error', 'Needs attention');
    setRequestStatus('error', `Forecast failed for ${attempt.label}. Your previous valid result has not changed.`);
    elements['chart-loading'].hidden = true;
    elements['error-panel'].hidden = false;
    elements['error-title'].textContent = `No forecast for ${attempt.label}`;
    elements['error-message'].textContent = humanizeError(error, attempt);
    if (state.currentResult) renderResult(state.currentResult, true);
    resetExplanation(state.currentResult
      ? `Explanation is unavailable until you return to the previous forecast or complete a new one.`
      : 'Complete a valid forecast before requesting an explanation.');
    updateForecastButtons();
  }

  function humanizeError(error, attempt) {
    const message = String(error?.message || '');
    if (message.includes('No recent five-minute')) {
      return `No recent five-minute data was found for ${attempt.submittedTicker} on ${attempt.exchange}. Recent intraday data may be delayed or unavailable. Check the ticker, try the other exchange, or upload a CSV.`;
    }
    if (message.includes('Kronos needs 400 valid market bars') || message.includes('Kronos needs 400 valid market records')) {
      return message.replace('market records', 'market bars');
    }
    if (message.includes('CSV is missing required columns')) return `${message} Add the missing fields and try again.`;
    if (message.includes('could not be read')) return message;
    if (message.includes('standard ticker')) return `Enter a standard NSE or BSE ticker using letters and numbers only.`;
    if (message.includes('Failed to fetch') || message.includes('local server')) {
      return 'The local dashboard server could not be reached. Check that it is running, then try again.';
    }
    return `Kronos could not complete this forecast. Try again, switch the exchange, or upload a CSV.`;
  }

  function commitSuccess(payload, requestId, initial = false) {
    if (!initial && requestId !== state.requestSequence) return;
    if (!initial && payload.request_id != null && Number(payload.request_id) !== requestId) return;

    const result = resultFromPayload(payload);
    if (!initial && state.pending?.id === requestId && state.pending.companyName) {
      result.company_name = state.pending.companyName;
    }
    state.currentResult = result;
    state.previousResult = null;
    state.pending = null;
    state.phase = 'success';
    elements['error-panel'].hidden = true;
    elements['chart-loading'].hidden = true;
    renderResult(result, false);
    resetExplanation('The forecast works without this optional step.');
    setReadiness('ready', 'Ready');
    setRequestStatus('ready', initial
      ? `Showing the latest saved forecast for ${result.normalized_symbol}.`
      : `Forecast complete for ${result.normalized_symbol}. All visible results were updated together.`);
    updateForecastButtons();
    loadCachedExplanation(result.summary_fingerprint, requestId || state.requestSequence);
  }

  function currentLiveKey() {
    const exchange = selectedValue('exchange') || state.exchange;
    return state.selectedListing?.symbol || normalizeAttempt(elements['ticker-input'].value, exchange);
  }

  function updateQuickTickerState() {
    const current = sanitizeTicker(elements['ticker-input'].value).replace(/\.(NS|BO)$/, '');
    document.querySelectorAll('.quick-ticker').forEach((button) => {
      button.setAttribute('aria-pressed', String(button.dataset.ticker === current));
    });
  }

  function updateForecastButtons() {
    const liveButton = elements['live-forecast-button'];
    const csvButton = elements['csv-forecast-button'];
    const liveKey = currentLiveKey();
    const pendingLive = state.pending?.source === 'live';
    const isDuplicateLive = pendingLive && state.pending.label === liveKey;

    liveButton.disabled = !sanitizeTicker(elements['ticker-input'].value) || Boolean(isDuplicateLive);
    liveButton.dataset.loading = String(Boolean(isDuplicateLive));
    const actionLabel = selectedMode() === 'validation' ? 'Run historical check' : 'Run forecast';
    setButtonLabel(liveButton, isDuplicateLive
      ? `${selectedMode() === 'validation' ? 'Checking historical performance for' : 'Forecasting'} ${state.pending.label}`
      : pendingLive ? actionLabel : actionLabel);

    const selectedFile = elements['csv-input'].files?.[0];
    const pendingCsv = state.pending?.source === 'csv';
    const isDuplicateCsv = pendingCsv && selectedFile && state.pending.filename === selectedFile.name;
    csvButton.disabled = !selectedFile || Boolean(isDuplicateCsv);
    csvButton.dataset.loading = String(Boolean(isDuplicateCsv));
    setButtonLabel(csvButton, isDuplicateCsv
      ? 'Processing CSV'
      : pendingCsv ? actionLabel : actionLabel);
  }

  function beginRequest(attempt) {
    if (state.pending?.key === attempt.key) return null;
    state.forecastController?.abort();
    state.explanationController?.abort();
    state.requestSequence += 1;
    state.forecastController = new AbortController();
    state.previousResult = state.currentResult;
    state.pending = { ...attempt, id: state.requestSequence };
    state.lastAttempt = attempt;
    renderLoading(state.pending);
    return state.pending;
  }

  async function fetchJson(url, options = {}) {
    const response = await fetch(url, { cache: 'no-store', ...options });
    let payload;
    try {
      payload = await response.json();
    } catch {
      payload = {};
    }
    if (!response.ok) {
      if (response.status === 401 && url !== '/api/auth/session' && url !== '/api/auth/sign-in') {
        showSignedOut(false, 'Your local session has expired. Continue again to reopen this profile.');
      }
      throw new Error(payload.error || 'The local server could not complete the request.');
    }
    return payload;
  }

  function showAuthError(message) {
    elements['auth-error'].textContent = message;
    elements['auth-error'].hidden = !message;
  }

  function showProfileError(message) {
    elements['profile-error'].textContent = message;
    elements['profile-error'].hidden = !message;
  }

  function showSignedOut(firstRun, message = '') {
    state.authFirstRun = Boolean(firstRun);
    state.profile = null;
    state.currentResult = null;
    state.previousResult = null;
    state.pending = null;
    state.forecastController?.abort();
    state.explanationController?.abort();
    state.searchController?.abort();
    state.requestSequence += 1;
    state.cachedExplanation = null;
    state.explanationVisible = false;
    state.searchResults = [];
    state.selectedListing = null;
    state.lastAttempt = null;
    state.sourceMode = 'live';
    state.forecastMode = 'live';
    state.horizonBars = 75;
    state.exchange = 'NSE';
    state.chartMode = 'candles';
    state.phase = 'signed-out';
    elements['dashboard-view'].hidden = true;
    elements['auth-screen'].hidden = false;
    elements['account-controls'].hidden = true;
    elements['account-name'].textContent = '';
    elements['auth-title'].textContent = firstRun
      ? 'Create a local research profile'
      : 'Continue to your research workspace';
    elements['auth-name'].required = Boolean(firstRun);
    elements['auth-name-label'].textContent = firstRun ? 'Name' : 'Name (needed for a new profile)';
    elements['auth-submit'].textContent = firstRun ? 'Create local profile' : 'Continue locally';
    showAuthError(message);
    if (elements['forecast-result']) elements['forecast-result'].hidden = true;
    if (elements['empty-state']) elements['empty-state'].hidden = false;
    if (elements['error-panel']) elements['error-panel'].hidden = true;
    if (elements['chart-loading']) elements['chart-loading'].hidden = true;
    if (elements['selected-symbol-help']) elements['selected-symbol-help'].textContent = '';
    if (elements['symbol-search-list']) closeSearchMenu();
    if (elements['ticker-input']) elements['ticker-input'].value = 'RELIANCE';
    if (elements['csv-input']) elements['csv-input'].value = '';
    if (elements['source-live']) elements['source-live'].checked = true;
    if (elements['source-csv']) elements['source-csv'].checked = false;
    if (elements['explanation-text']) resetExplanation('Sign in to view this profile’s saved explanation.');
    (firstRun ? elements['auth-name'] : elements['auth-email']).focus();
  }

  function applyProfilePreferences(profile) {
    state.profile = profile;
    const preferences = profile.preferences || {};
    const exchange = preferences.preferred_exchange === 'BSE' ? 'BSE' : 'NSE';
    const mode = preferences.default_research_mode === 'historical' ? 'validation' : 'live';
    const horizon = [24, 75, 120].includes(Number(preferences.default_horizon))
      ? Number(preferences.default_horizon)
      : 75;
    elements['exchange-nse'].checked = exchange === 'NSE';
    elements['exchange-bse'].checked = exchange === 'BSE';
    elements['mode-live'].checked = mode === 'live';
    elements['mode-validation'].checked = mode === 'validation';
    document.querySelectorAll('input[name="forecast-horizon"]').forEach((input) => {
      input.checked = Number(input.value) === horizon;
    });
    elements['source-live'].checked = true;
    elements['source-csv'].checked = false;
    state.exchange = exchange;
    state.forecastMode = mode;
    state.horizonBars = horizon;
    setChartMode(preferences.chart_mode === 'line' ? 'line' : 'candles');
    setSourceMode('live');
    updateModeAndHorizonCopy();
    updateQuickTickerState();
  }

  async function restoreLocalSession() {
    try {
      const result = await fetchJson('/api/auth/session');
      if (result.authenticated && result.profile) {
        enterWorkspace(result.profile);
      } else {
        showSignedOut(Boolean(result.first_run));
      }
    } catch (error) {
      showSignedOut(true, `The local server could not be reached. ${error.message}`);
    }
  }

  function enterWorkspace(profile) {
    state.authFirstRun = false;
    elements['auth-screen'].hidden = true;
    elements['dashboard-view'].hidden = false;
    elements['account-controls'].hidden = false;
    elements['account-name'].textContent = profile.name;
    elements['account-name'].title = profile.email;
    elements['auth-email'].value = '';
    elements['auth-name'].value = '';
    showAuthError('');
    applyProfilePreferences(profile);
    loadInitialDashboard();
  }

  function openProfileDialog() {
    const profile = state.profile;
    if (!profile) return;
    const preferences = profile.preferences || {};
    elements['profile-name'].value = profile.name || '';
    elements['profile-email'].value = profile.email || '';
    elements['profile-exchange'].value = preferences.preferred_exchange || 'NSE';
    elements['profile-chart-mode'].value = preferences.chart_mode || 'candles';
    elements['profile-currency'].value = preferences.currency_display || 'symbol';
    elements['profile-volume'].checked = preferences.show_volume !== false;
    elements['profile-horizon'].value = String(preferences.default_horizon || 75);
    elements['profile-mode'].value = preferences.default_research_mode || 'forecast';
    showProfileError('');
    elements['profile-dialog'].showModal();
    elements['profile-name'].focus();
  }

  async function submitLocalSignIn(event) {
    event.preventDefault();
    showAuthError('');
    elements['auth-submit'].disabled = true;
    elements['auth-submit'].textContent = 'Opening local profile…';
    try {
      const result = await fetchJson('/api/auth/sign-in', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          email: elements['auth-email'].value,
          name: elements['auth-name'].value,
        }),
      });
      enterWorkspace(result.profile);
    } catch (error) {
      showAuthError(error.message);
    } finally {
      elements['auth-submit'].disabled = false;
      elements['auth-submit'].textContent = state.authFirstRun ? 'Create local profile' : 'Continue locally';
    }
  }

  async function saveProfile(event) {
    event.preventDefault();
    showProfileError('');
    const preferences = {
      preferred_exchange: elements['profile-exchange'].value,
      chart_mode: elements['profile-chart-mode'].value,
      currency_display: elements['profile-currency'].value,
      show_volume: elements['profile-volume'].checked,
      default_horizon: Number(elements['profile-horizon'].value),
      default_research_mode: elements['profile-mode'].value,
    };
    try {
      const result = await fetchJson('/api/auth/profile', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: elements['profile-name'].value, preferences }),
      });
      applyProfilePreferences(result.profile);
      elements['account-name'].textContent = result.profile.name;
      elements['account-name'].title = result.profile.email;
      elements['profile-dialog'].close();
      if (state.currentResult) renderResult(state.currentResult, false);
      setRequestStatus('ready', 'Profile preferences saved for this local profile.');
    } catch (error) {
      showProfileError(error.message);
    }
  }

  async function signOut() {
    const button = elements['sign-out-button'];
    button.disabled = true;
    try {
      await fetchJson('/api/auth/sign-out', { method: 'POST' });
      showSignedOut(false);
    } catch (error) {
      setRequestStatus('error', `Could not sign out: ${error.message}`);
    } finally {
      button.disabled = false;
    }
  }

  function bindAuthEvents() {
    elements['auth-form'].addEventListener('submit', submitLocalSignIn);
    elements['sign-out-button'].addEventListener('click', signOut);
    elements['profile-open'].addEventListener('click', openProfileDialog);
    elements['profile-close'].addEventListener('click', () => elements['profile-dialog'].close());
    elements['profile-cancel'].addEventListener('click', () => elements['profile-dialog'].close());
    elements['profile-form'].addEventListener('submit', saveProfile);
  }

  function setSelectedListing(listing) {
    state.selectedListing = listing;
    elements['ticker-input'].value = listing.baseSymbol || listing.symbol;
    elements['selected-symbol-help'].textContent = `${listing.name} · ${listing.symbol} · ${listing.exchange}`;
    if (listing.exchange === 'NSE' || listing.exchange === 'BSE') {
      elements[`exchange-${listing.exchange.toLowerCase()}`].checked = true;
      state.exchange = listing.exchange;
    }
    closeSearchMenu();
    updateQuickTickerState();
    updateForecastButtons();
  }

  function clearSelectedListing() {
    state.selectedListing = null;
    elements['selected-symbol-help'].textContent = '';
  }

  function closeSearchMenu() {
    elements['symbol-search-list'].hidden = true;
    elements['ticker-input'].setAttribute('aria-expanded', 'false');
    elements['ticker-input'].removeAttribute('aria-activedescendant');
    state.activeSearchIndex = -1;
  }

  function renderSearchResults(results, message = '') {
    const list = elements['symbol-search-list'];
    list.replaceChildren();
    state.searchResults = results;
    if (!results.length) {
      if (!message) return closeSearchMenu();
      const empty = document.createElement('div');
      empty.className = 'search-empty';
      empty.textContent = message;
      list.append(empty);
      list.hidden = false;
      elements['ticker-input'].setAttribute('aria-expanded', 'true');
      return;
    }
    results.forEach((result, index) => {
      const option = document.createElement('button');
      option.type = 'button';
      option.id = `symbol-option-${index}`;
      option.className = 'search-option';
      option.setAttribute('role', 'option');
      option.setAttribute('aria-selected', String(index === state.activeSearchIndex));
      option.innerHTML = `<span class="search-option__name"></span><span class="search-option__badge"></span><span class="search-option__symbol"></span>`;
      option.querySelector('.search-option__name').textContent = result.name;
      option.querySelector('.search-option__badge').textContent = result.exchange;
      option.querySelector('.search-option__symbol').textContent = result.symbol;
      option.addEventListener('mousedown', (event) => event.preventDefault());
      option.addEventListener('click', () => setSelectedListing(result));
      list.append(option);
    });
    list.hidden = false;
    elements['ticker-input'].setAttribute('aria-expanded', 'true');
  }

  function setActiveSearchIndex(index) {
    if (!state.searchResults.length) return;
    state.activeSearchIndex = (index + state.searchResults.length) % state.searchResults.length;
    document.querySelectorAll('.search-option').forEach((option, optionIndex) => {
      option.setAttribute('aria-selected', String(optionIndex === state.activeSearchIndex));
    });
    elements['ticker-input'].setAttribute('aria-activedescendant', `symbol-option-${state.activeSearchIndex}`);
  }

  async function searchSymbols(query, { immediate = false } = {}) {
    const cleaned = query.trim();
    const usefulLength = usefulSearchLength(cleaned);
    state.searchController?.abort();
    if (usefulLength < 2) {
      elements['symbol-search-spinner'].parentElement.dataset.loading = 'false';
      state.searchResults = [];
      return [];
    }
    state.searchSequence += 1;
    const requestId = state.searchSequence;
    state.searchController = new AbortController();
    elements['symbol-search-spinner'].parentElement.dataset.loading = 'true';
    try {
      const payload = await fetchJson(`/api/symbol-search?q=${encodeURIComponent(cleaned)}&exchange=${encodeURIComponent(selectedValue('exchange') || state.exchange)}`, {
        signal: state.searchController.signal,
      });
      if (requestId !== state.searchSequence) return [];
      const results = payload.results || [];
      renderSearchResults(results, results.length ? '' : 'No matching Indian equity found. Try the company name or its NSE/BSE symbol.');
      elements['selected-symbol-help'].textContent = results.length ? `${results.length} matching Indian equities found.` : '';
      if (immediate && results.length === 1) setSelectedListing(results[0]);
      return results;
    } catch (error) {
      if (error.name !== 'AbortError' && requestId === state.searchSequence) {
        renderSearchResults([], 'No matching Indian equity found. Try the company name or its NSE/BSE symbol.');
      }
      return [];
    } finally {
      if (requestId === state.searchSequence) elements['symbol-search-spinner'].parentElement.dataset.loading = 'false';
    }
  }

  function queueSymbolSearch() {
    window.clearTimeout(state.searchTimer);
    const query = elements['ticker-input'].value;
    if (usefulSearchLength(query) < 2) return closeSearchMenu();
    state.searchTimer = window.setTimeout(() => searchSymbols(query), 300);
  }

  function highConfidenceMatch(query, results) {
    const clean = query.trim().toUpperCase();
    if (results.length === 1) return results[0];
    const exact = results.find((result) => (
      result.symbol.toUpperCase() === clean ||
      result.baseSymbol.toUpperCase() === clean ||
      result.name.toUpperCase() === clean
    ));
    if (exact) return exact;
    const bases = new Set(results.map((result) => result.baseSymbol.toUpperCase()));
    if (bases.size === 1) {
      return results.find((result) => result.exchange === (selectedValue('exchange') || state.exchange));
    }
    return null;
  }

  async function runLiveForecast(event) {
    event?.preventDefault?.();
    const inputValue = elements['ticker-input'].value.trim();
    const exchange = selectedValue('exchange') || 'NSE';
    if (!inputValue) {
      setRequestStatus('error', 'Enter a company name or ticker before running a forecast.');
      elements['ticker-input'].focus();
      return;
    }

    let listing = state.selectedListing;
    if (!listing && !isSymbolLike(inputValue)) {
      const matches = await searchSymbols(inputValue, { immediate: true });
      listing = highConfidenceMatch(inputValue, matches);
      if (!listing) {
        setRequestStatus('error', matches.length
          ? 'Select the correct company from the search results before forecasting.'
          : 'No matching Indian equity found. Try the company name or its NSE/BSE symbol.');
        if (state.currentResult) renderResult(state.currentResult, true);
        return;
      }
    }

    const ticker = listing ? listing.symbol : sanitizeTicker(inputValue);
    const label = listing ? listing.symbol : normalizeAttempt(ticker, exchange);
    const mode = selectedMode();
    const horizon = selectedHorizon();
    const request = beginRequest({
      source: 'live',
      key: `${mode}:live:${label}:${horizon}`,
      label,
      submittedTicker: inputValue.replace(/\.(NS|BO)$/i, ''),
      exchange: listing?.exchange || exchange,
      companyName: listing?.name || '',
    });
    if (!request) return;

    try {
      const result = await fetchJson(mode === 'validation' ? '/api/live-validation' : '/api/live-forecast', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ticker, exchange: request.exchange, request_id: request.id, forecast_bars: horizon }),
        signal: state.forecastController.signal,
      });
      commitSuccess(result, request.id);
    } catch (error) {
      if (error.name !== 'AbortError' && request.id === state.requestSequence) renderError(error, request);
    } finally {
      if (request.id === state.requestSequence && state.phase !== 'loading') state.forecastController = null;
      updateForecastButtons();
    }
  }

  async function runCsvForecast(event) {
    event?.preventDefault?.();
    const file = elements['csv-input'].files?.[0];
    if (!file) {
      setRequestStatus('error', 'Select a CSV file before running a forecast.');
      return;
    }
    const mode = selectedMode();
    const horizon = selectedHorizon();
    const request = beginRequest({
      source: 'csv',
      key: `${mode}:csv:${file.name}:${file.size}:${file.lastModified}:${horizon}`,
      label: file.name,
      filename: file.name,
      submittedTicker: file.name,
      exchange: 'CSV',
    });
    if (!request) return;

    try {
      const csv = await file.text();
      if (request.id !== state.requestSequence) return;
      const result = await fetchJson(mode === 'validation' ? '/api/validation' : '/api/forecast', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ csv, filename: file.name, request_id: request.id, forecast_bars: horizon }),
        signal: state.forecastController.signal,
      });
      commitSuccess(result, request.id);
    } catch (error) {
      if (error.name !== 'AbortError' && request.id === state.requestSequence) renderError(error, request);
    } finally {
      if (request.id === state.requestSequence && state.phase !== 'loading') state.forecastController = null;
      updateForecastButtons();
    }
  }

  function resetExplanation(statusText) {
    state.cachedExplanation = null;
    state.explanationVisible = false;
    elements['explanation-badge'].textContent = 'Optional';
    elements['explanation-text'].textContent = state.currentResult
      ? `Request a concise explanation for ${state.currentResult.normalized_symbol}.`
      : 'Run a forecast to request a concise explanation.';
    elements['explanation-status'].textContent = statusText;
    elements['explain-button'].disabled = !state.currentResult || state.phase !== 'success';
    elements['explain-button'].dataset.loading = 'false';
    setButtonLabel(elements['explain-button'], 'Explain forecast');
  }

  async function loadCachedExplanation(fingerprint, requestId) {
    if (!fingerprint || state.phase !== 'success') return;
    try {
      const cached = await fetchJson('/api/explanation');
      if (
        requestId !== state.requestSequence ||
        state.currentResult?.summary_fingerprint !== fingerprint ||
        cached.summary_fingerprint !== fingerprint
      ) return;
      if (cached.available) {
        state.cachedExplanation = cached.explanation;
        elements['explanation-badge'].textContent = 'Saved locally';
        elements['explanation-text'].textContent = `A saved explanation is available for ${state.currentResult.normalized_symbol}.`;
        elements['explanation-status'].textContent = 'Use it without another API call.';
        elements['explain-button'].disabled = false;
        elements['explain-button'].classList.add('button--secondary');
        setButtonLabel(elements['explain-button'], 'Use saved explanation');
      }
    } catch {
      elements['explanation-status'].textContent = 'Saved explanation is unavailable. The forecast is unaffected.';
    }
  }

  async function requestExplanation() {
    if (!state.currentResult || state.phase !== 'success') return;
    if (state.cachedExplanation && !state.explanationVisible) {
      state.explanationVisible = true;
      elements['explanation-text'].textContent = state.cachedExplanation;
      elements['explanation-status'].textContent = `Saved explanation for ${state.currentResult.normalized_symbol}. No API call used.`;
      setButtonLabel(elements['explain-button'], 'Saved explanation shown');
      elements['explain-button'].disabled = true;
      return;
    }

    const fingerprint = state.currentResult.summary_fingerprint;
    const requestId = state.requestSequence;
    state.explanationController?.abort();
    state.explanationController = new AbortController();
    elements['explain-button'].disabled = true;
    elements['explain-button'].dataset.loading = 'true';
    setButtonLabel(elements['explain-button'], 'Creating explanation');
    elements['explanation-status'].textContent = `Explaining the saved Kronos result for ${state.currentResult.normalized_symbol}.`;

    let failed = false;
    try {
      const result = await fetchJson('/api/explanation', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ summary_fingerprint: fingerprint }),
        signal: state.explanationController.signal,
      });
      if (
        requestId !== state.requestSequence ||
        state.currentResult?.summary_fingerprint !== fingerprint ||
        result.summary_fingerprint !== fingerprint
      ) return;
      state.cachedExplanation = result.explanation;
      state.explanationVisible = true;
      elements['explanation-badge'].textContent = result.cached ? 'Saved locally' : 'New explanation';
      elements['explanation-text'].textContent = result.explanation;
      elements['explanation-status'].textContent = result.cached
        ? 'Loaded without another API call.'
        : 'Saved locally for this forecast.';
    } catch (error) {
      if (error.name !== 'AbortError' && requestId === state.requestSequence) {
        failed = true;
        elements['explanation-badge'].textContent = 'Unavailable';
        elements['explanation-status'].textContent = 'The explanation service is unavailable. The Kronos forecast remains complete.';
      }
    } finally {
      if (requestId === state.requestSequence) {
        elements['explain-button'].dataset.loading = 'false';
        elements['explain-button'].disabled = failed || state.explanationVisible;
        setButtonLabel(
          elements['explain-button'],
          failed ? 'Explanation unavailable' : state.explanationVisible ? 'Explanation shown' : 'Explain forecast',
        );
      }
    }
  }

  function setSourceMode(mode) {
    state.sourceMode = mode;
    const isLive = mode === 'live';
    elements['live-form'].hidden = !isLive;
    elements['csv-form'].hidden = isLive;
    elements['quick-tickers'].hidden = !isLive;
    elements['exchange-control'].disabled = !isLive;
    if (state.phase !== 'loading') {
      setRequestStatus('ready', isLive
        ? 'Yahoo market selected. Search by company name or NSE/BSE ticker.'
        : elements['csv-input'].files?.[0]
          ? `CSV selected: ${elements['csv-input'].files[0].name}.`
          : 'CSV mode selected. Choose a file with at least 400 valid market bars.');
    }
    updateForecastButtons();
  }

  function updateModeAndHorizonCopy() {
    state.forecastMode = selectedMode();
    state.horizonBars = selectedHorizon();
    const horizonText = state.horizonBars === 24 ? '2 hours' : state.horizonBars === 120 ? '120 market bars' : 'Next 75 market bars';
    if (elements['local-note']) elements['local-note'].textContent = `About 5 trading days → ${horizonText}`;
    if (state.phase !== 'loading') {
      setRequestStatus('ready', state.forecastMode === 'validation'
        ? `Historical performance selected. Kronos will forecast the final ${state.horizonBars} bars, then compare them with the observed future window.`
        : `Live forecast selected. Kronos will predict ${state.horizonBars} future market bars.`);
    }
    updateForecastButtons();
  }

  function switchExchange() {
    const next = selectedValue('exchange') === 'NSE' ? elements['exchange-bse'] : elements['exchange-nse'];
    next.checked = true;
    state.exchange = next.value;
    updateForecastButtons();
    setRequestStatus('ready', `${next.value} selected. Review the ticker and run the forecast.`);
    elements['ticker-input'].focus();
  }

  function showCsvMode() {
    elements['source-csv'].checked = true;
    setSourceMode('csv');
    elements['csv-input'].focus();
  }

  function retryLastAttempt() {
    if (!state.lastAttempt) return;
    if (state.lastAttempt.source === 'csv') runCsvForecast();
    else runLiveForecast();
  }

  function timeLabel(timestamp) {
    const date = new Date(timestamp);
    if (Number.isNaN(date.getTime())) return '';
    return date.toLocaleTimeString('en-IN', { timeZone: 'Asia/Kolkata', hour: 'numeric', minute: '2-digit', hour12: true });
  }

  function shortDateTimeLabel(timestamp) {
    const date = new Date(timestamp);
    if (Number.isNaN(date.getTime())) return '';
    return `${date.toLocaleDateString('en-IN', { timeZone: 'Asia/Kolkata', day: 'numeric', month: 'short' })}, ${timeLabel(timestamp)} IST`;
  }

  function hasOhlc(point) {
    return ['open', 'high', 'low', 'close'].every((key) => Number.isFinite(Number(point[key])));
  }

  function drawPolyline(context, points, startIndex, x, y, color, dashed = false) {
    if (!points.length) return;
    context.strokeStyle = color;
    context.lineWidth = dashed ? 2.5 : 2;
    context.setLineDash(dashed ? [7, 5] : []);
    context.beginPath();
    points.forEach((point, index) => {
      const pointX = x(startIndex + index);
      const pointY = y(Number(point.close));
      const previous = points[index - 1];
      const gap = previous ? (new Date(point.timestamp) - new Date(previous.timestamp)) / 60000 : 5;
      if (index === 0 || (dashed && (gap > 5 || !sameMarketDate(point.timestamp, previous.timestamp)))) context.moveTo(pointX, pointY);
      else context.lineTo(pointX, pointY);
    });
    context.stroke();
    context.setLineDash([]);
  }

  function drawChart(result) {
    const canvas = elements['forecast-chart'];
    const container = elements['chart-container'];
    const observed = (result.chart?.observed || []).filter((point) => Number.isFinite(Number(point.close)));
    const forecast = (result.chart?.forecast || []).filter((point) => Number.isFinite(Number(point.close)));
    const actual = (result.validation?.actual || result.chart?.actual || []).filter((point) => Number.isFinite(Number(point.close)));
    const candleMode = state.chartMode === 'candles' && [...observed, ...forecast].some(hasOhlc);
    const priceValues = candleMode
      ? [...observed, ...forecast, ...actual].flatMap((point) => hasOhlc(point) ? [Number(point.high), Number(point.low)] : [Number(point.close)])
      : [...observed.map((point) => Number(point.close)), ...forecast.map((point) => Number(point.close)), ...actual.map((point) => Number(point.close))];
    const values = priceValues.filter(Number.isFinite);
    if (!values.length) return;

    const bounds = container.getBoundingClientRect();
    const width = Math.max(bounds.width, 280);
    const height = Math.max(bounds.height, 240);
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.round(width * ratio);
    canvas.height = Math.round(height * ratio);
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;
    const context = canvas.getContext('2d');
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    context.clearRect(0, 0, width, height);

    const compact = width < 560;
    const inset = { top: 36, right: compact ? 12 : 22, bottom: 34, left: compact ? 48 : 62 };
    const plotWidth = width - inset.left - inset.right;
    const volumeValues = [...observed, ...forecast].map((point) => Number(point.volume)).filter((value) => Number.isFinite(value) && value >= 0);
    const showVolume = candleMode && state.profile?.preferences?.show_volume !== false && volumeValues.some((value) => value > 0);
    const volumeHeight = showVolume ? (compact ? 42 : 54) : 0;
    const volumeGap = showVolume ? 18 : 0;
    const priceBottom = height - inset.bottom - volumeHeight - volumeGap;
    const plotHeight = priceBottom - inset.top;
    const rawMin = Math.min(...values);
    const rawMax = Math.max(...values);
    const padding = Math.max((rawMax - rawMin) * 0.16, Math.abs(rawMax) * 0.0005, 0.01);
    const minimum = rawMin - padding;
    const maximum = rawMax + padding;
    const range = maximum - minimum;
    const pointCount = Math.max(observed.length + forecast.length, 2);
    const x = (index) => inset.left + (index / (pointCount - 1)) * plotWidth;
    const y = (value) => inset.top + ((maximum - value) / range) * plotHeight;
    const volumeTop = priceBottom + volumeGap;
    const maxVolume = showVolume ? Math.max(...volumeValues, 1) : 1;
    const volumeY = (value) => volumeTop + (1 - (Number(value) || 0) / maxVolume) * volumeHeight;
    const boundaryIndex = forecast.length ? observed.length : Math.max(observed.length - 1, 0);
    const boundaryX = x(boundaryIndex);
    const chartColors = {
      grid: cssVar('--chart-grid') || '#e4e7ea',
      axis: cssVar('--chart-axis') || '#69717b',
      observed: cssVar('--chart-observed') || '#4f5965',
      region: cssVar('--chart-forecast-region') || 'rgba(20, 87, 166, 0.045)',
      labelBg: cssVar('--chart-label-bg') || '#ffffff',
      labelBorder: cssVar('--chart-label-border') || '#d6dbe0',
      labelText: cssVar('--text-secondary') || '#4f5965',
      positive: cssVar('--positive') || '#176b45',
      negative: cssVar('--negative') || '#a83a37',
      neutral: cssVar('--neutral') || '#5f6670',
      forecast: cssVar('--accent') || '#315f67',
      actual: cssVar('--warning') || '#9a6a08',
    };

    if (forecast.length) {
      context.fillStyle = chartColors.region;
      context.fillRect(boundaryX, inset.top, width - inset.right - boundaryX, plotHeight);
    }

    context.font = `${compact ? 10 : 11}px Inter, Segoe UI, system-ui, sans-serif`;
    context.textBaseline = 'middle';
    context.lineWidth = 1;
    for (let index = 0; index < 5; index += 1) {
      const gridY = inset.top + (index / 4) * plotHeight;
      const labelValue = maximum - (index / 4) * range;
      context.strokeStyle = chartColors.grid;
      context.beginPath();
      context.moveTo(inset.left, gridY);
      context.lineTo(width - inset.right, gridY);
      context.stroke();
      context.fillStyle = chartColors.axis;
      context.textAlign = 'right';
      context.fillText(formatAxisPrice(labelValue), inset.left - 8, gridY);
    }

    const forecastColor = chartColors.forecast;
    const candleWidth = Math.max(3, Math.min(compact ? 6 : 8, plotWidth / Math.max(pointCount, 1) * 0.58));

    if (candleMode) {
      const drawCandle = (point, index, color, predicted = false) => {
        if (!hasOhlc(point)) return;
        const pointX = x(index);
        const openY = y(Number(point.open));
        const closeY = y(Number(point.close));
        const highY = y(Number(point.high));
        const lowY = y(Number(point.low));
        const bodyTop = Math.min(openY, closeY);
        const bodyHeight = Math.max(Math.abs(closeY - openY), 2);
        context.strokeStyle = color;
        context.fillStyle = predicted ? chartColors.region : 'transparent';
        context.lineWidth = predicted ? 1.6 : 1.4;
        context.setLineDash(predicted ? [3, 2] : []);
        context.beginPath();
        context.moveTo(pointX, highY);
        context.lineTo(pointX, lowY);
        context.stroke();
        context.fillRect(pointX - candleWidth / 2, bodyTop, candleWidth, bodyHeight);
        context.strokeRect(pointX - candleWidth / 2, bodyTop, candleWidth, bodyHeight);
        context.setLineDash([]);
      };
      observed.forEach((point, index) => drawCandle(point, index, chartColors.observed));
      forecast.forEach((point, index) => drawCandle(point, observed.length + index, forecastColor, true));
      if (showVolume) {
        context.strokeStyle = chartColors.grid;
        context.beginPath();
        context.moveTo(inset.left, volumeTop);
        context.lineTo(width - inset.right, volumeTop);
        context.stroke();
        context.fillStyle = chartColors.axis;
        context.textAlign = 'right';
        context.fillText(formatVolume(maxVolume), inset.left - 8, volumeTop + 4);
        const drawVolume = (point, index, color) => {
          const value = Number(point.volume);
          if (!Number.isFinite(value)) return;
          const barTop = volumeY(value);
          context.fillStyle = color;
          context.globalAlpha = 0.42;
          context.fillRect(x(index) - candleWidth / 2, barTop, candleWidth, volumeTop + volumeHeight - barTop);
          context.globalAlpha = 1;
        };
        observed.forEach((point, index) => drawVolume(point, index, chartColors.observed));
        forecast.forEach((point, index) => drawVolume(point, observed.length + index, forecastColor));
        context.fillStyle = chartColors.axis;
        context.textAlign = 'left';
        context.fillText('Volume', inset.left, volumeTop - 7);
      }
    } else {
      drawPolyline(context, observed, 0, x, y, chartColors.observed, false);
    }

    context.strokeStyle = chartColors.axis;
    context.lineWidth = 1;
    context.setLineDash([4, 4]);
    context.beginPath();
    context.moveTo(boundaryX, inset.top - 4);
    context.lineTo(boundaryX, height - inset.bottom);
    context.stroke();

    if (forecast.length) {
      if (!candleMode) drawPolyline(context, forecast, observed.length, x, y, forecastColor, true);
      context.setLineDash([]);
      const finalIndex = observed.length + forecast.length - 1;
      context.fillStyle = forecastColor;
      context.beginPath();
      context.arc(x(finalIndex), y(Number(forecast[forecast.length - 1].close)), 3.5, 0, Math.PI * 2);
      context.fill();

      context.font = `650 ${compact ? 10 : 11}px Inter, Segoe UI, system-ui, sans-serif`;
      context.fillStyle = forecastColor;
      context.textAlign = 'left';
      context.textBaseline = 'middle';
      const predictedX = Math.min(boundaryX + 10, width - inset.right - 58);
      context.fillText('Predicted', Math.max(predictedX, inset.left), inset.top + 18);
    }

    if (actual.length) {
      drawPolyline(context, actual, observed.length, x, y, chartColors.actual, false);
      context.fillStyle = chartColors.actual;
      context.font = `650 ${compact ? 10 : 11}px Inter, Segoe UI, system-ui, sans-serif`;
      context.textAlign = 'left';
      context.fillText('Actual future', Math.max(boundaryX + 10, inset.left), inset.top + (compact ? 34 : 38));
    }

    let boundaryLabel = 'Kronos prediction starts';
    context.font = `600 ${compact ? 10 : 11}px Inter, Segoe UI, system-ui, sans-serif`;
    const maxLabelWidth = width - inset.left - inset.right;
    if (context.measureText(boundaryLabel).width + 12 > maxLabelWidth) boundaryLabel = 'Prediction starts';
    const labelWidth = Math.min(context.measureText(boundaryLabel).width + 12, maxLabelWidth);
    const labelX = Math.min(Math.max(boundaryX - labelWidth / 2, inset.left), width - inset.right - labelWidth);
    context.fillStyle = chartColors.labelBg;
    context.fillRect(labelX, 6, labelWidth, 20);
    context.strokeStyle = chartColors.labelBorder;
    context.setLineDash([]);
    context.strokeRect(labelX, 6, labelWidth, 20);
    context.fillStyle = chartColors.labelText;
    context.textAlign = 'center';
    context.fillText(boundaryLabel, labelX + labelWidth / 2, 16);

    const firstPoint = observed[0] || forecast[0];
    const lastPoint = forecast[forecast.length - 1] || observed[observed.length - 1];
    context.font = `${compact ? 10 : 11}px Inter, Segoe UI, system-ui, sans-serif`;
    context.fillStyle = chartColors.axis;
    context.textBaseline = 'alphabetic';
    context.textAlign = 'left';
    context.fillText(compact ? timeLabel(firstPoint.timestamp) : shortDateTimeLabel(firstPoint.timestamp), inset.left, height - 8);
    context.textAlign = 'right';
    context.fillText(compact ? shortDateTimeLabel(lastPoint.timestamp) : shortDateTimeLabel(lastPoint.timestamp), width - inset.right, height - 8);
  }

  async function loadInitialDashboard() {
    try {
      const result = await fetchJson('/api/dashboard');
      commitSuccess(result, state.requestSequence, true);
    } catch {
      state.phase = 'ready';
      setReadiness('ready', 'Ready');
      setRequestStatus('ready', 'Ready for a new local forecast.');
      resetExplanation('The forecast works without this optional step.');
    }
  }

  function bindEvents() {
    elements['theme-light'].addEventListener('click', () => setTheme('light', true));
    elements['theme-dark'].addEventListener('click', () => setTheme('dark', true));
    elements['chart-candles'].addEventListener('click', () => setChartMode('candles'));
    elements['chart-line'].addEventListener('click', () => setChartMode('line'));
    window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', (event) => {
      if (!localStorage.getItem('kronos-theme')) setTheme(event.matches ? 'dark' : 'light');
    });
    elements['live-form'].addEventListener('submit', runLiveForecast);
    elements['csv-form'].addEventListener('submit', runCsvForecast);
    elements['explain-button'].addEventListener('click', requestExplanation);
    elements['retry-button'].addEventListener('click', retryLastAttempt);
    elements['switch-exchange-button'].addEventListener('click', switchExchange);
    elements['error-csv-button'].addEventListener('click', showCsvMode);

    document.querySelectorAll('input[name="source-mode"]').forEach((input) => {
      input.addEventListener('change', () => setSourceMode(selectedValue('source-mode')));
    });
    document.querySelectorAll('input[name="forecast-mode"]').forEach((input) => {
      input.addEventListener('change', updateModeAndHorizonCopy);
    });
    document.querySelectorAll('input[name="forecast-horizon"]').forEach((input) => {
      input.addEventListener('change', updateModeAndHorizonCopy);
    });
    document.querySelectorAll('input[name="exchange"]').forEach((input) => {
      input.addEventListener('change', () => {
        state.exchange = selectedValue('exchange');
        if (state.selectedListing && state.selectedListing.exchange !== state.exchange) {
          clearSelectedListing();
          setRequestStatus('ready', `${state.exchange} selected. Choose a matching listing before forecasting.`);
        }
        updateForecastButtons();
        if (state.phase !== 'loading' && !state.selectedListing) setRequestStatus('ready', `${state.exchange} selected. Run the forecast when ready.`);
        queueSymbolSearch();
      });
    });
    elements['ticker-input'].addEventListener('input', () => {
      clearSelectedListing();
      updateQuickTickerState();
      updateForecastButtons();
      queueSymbolSearch();
    });
    elements['ticker-input'].addEventListener('keydown', (event) => {
      if (event.key === 'ArrowDown') {
        event.preventDefault();
        setActiveSearchIndex(state.activeSearchIndex + 1);
      } else if (event.key === 'ArrowUp') {
        event.preventDefault();
        setActiveSearchIndex(state.activeSearchIndex - 1);
      } else if (event.key === 'Enter' && state.activeSearchIndex >= 0 && state.searchResults[state.activeSearchIndex]) {
        event.preventDefault();
        setSelectedListing(state.searchResults[state.activeSearchIndex]);
      } else if (event.key === 'Escape') {
        closeSearchMenu();
      }
    });
    elements['live-form'].addEventListener('focusout', () => {
      window.setTimeout(() => {
        if (!elements['live-form'].contains(document.activeElement)) closeSearchMenu();
      }, 120);
    });
    elements['csv-input'].addEventListener('change', () => {
      const file = elements['csv-input'].files?.[0];
      setRequestStatus(file ? 'ready' : 'error', file
        ? `CSV selected: ${file.name}. Ready for local validation and forecasting.`
        : 'Choose a CSV file before running a forecast.');
      updateForecastButtons();
    });
    document.querySelectorAll('.quick-ticker').forEach((button) => {
      button.addEventListener('click', () => {
        elements['ticker-input'].value = button.dataset.ticker;
        clearSelectedListing();
        closeSearchMenu();
        updateQuickTickerState();
        updateForecastButtons();
        elements['ticker-input'].focus();
      });
    });

    const redrawChart = () => {
      if (state.currentResult && !elements['forecast-result'].hidden) drawChart(state.currentResult);
    };
    if ('ResizeObserver' in window) {
      const resizeObserver = new ResizeObserver(redrawChart);
      resizeObserver.observe(elements['chart-container']);
    } else {
      window.addEventListener('resize', redrawChart, { passive: true });
    }
  }

  function initialize() {
    cacheElements();
    setTheme(document.documentElement.dataset.theme || 'light');
    bindEvents();
    bindAuthEvents();
    window.kronosDashboard = {
      getState: () => ({
        phase: state.phase,
        localDemo: true,
        signedIn: Boolean(state.profile),
        requestSequence: state.requestSequence,
        pending: state.pending ? { ...state.pending } : null,
        currentSymbol: state.currentResult?.normalized_symbol || null,
        currentFingerprint: state.currentResult?.summary_fingerprint || null,
      }),
    };
    restoreLocalSession();
  }

  document.addEventListener('DOMContentLoaded', initialize);
})();
