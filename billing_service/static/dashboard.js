(function () {
  "use strict";

  const config = window.NM_BILLING_DASHBOARD || {};
  const state = { payload: null, loading: false };
  const numberFormat = new Intl.NumberFormat("en-GB");

  function byId(id) {
    return document.getElementById(id);
  }

  function valueAsNumber(value) {
    const numeric = Number(value);
    return Number.isFinite(numeric) ? numeric : 0;
  }

  function humanize(value) {
    return String(value || "")
      .replace(/[_-]+/g, " ")
      .replace(/\b\w/g, function (match) {
        return match.toUpperCase();
      })
      .trim();
  }

  function formatTokens(value) {
    return numberFormat.format(Math.round(valueAsNumber(value))) + " tok";
  }

  function formatMoney(minor, currency) {
    const resolvedCurrency = String(currency || "GBP").trim() || "GBP";
    return new Intl.NumberFormat("en-GB", {
      style: "currency",
      currency: resolvedCurrency,
      maximumFractionDigits: 2,
    }).format(valueAsNumber(minor) / 100);
  }

  function formatCount(value) {
    return numberFormat.format(Math.round(valueAsNumber(value)));
  }

  function formatPercent(value, digits) {
    const precision = Number.isFinite(digits) ? digits : 0;
    return (valueAsNumber(value) * 100).toFixed(precision) + "%";
  }

  function formatScore(value) {
    return valueAsNumber(value).toFixed(2);
  }

  function formatDate(value, includeTime) {
    if (!value) {
      return "-";
    }
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
      return String(value);
    }
    const options = includeTime
      ? {
          year: "numeric",
          month: "short",
          day: "numeric",
          hour: "2-digit",
          minute: "2-digit",
        }
      : {
          year: "numeric",
          month: "short",
          day: "numeric",
        };
    return new Intl.DateTimeFormat("en-GB", options).format(date);
  }

  function toneFromRisk(value) {
    const risk = valueAsNumber(value);
    if (risk >= 0.8) {
      return "danger";
    }
    if (risk >= 0.62) {
      return "warn";
    }
    if (risk >= 0.38) {
      return "watch";
    }
    return "ok";
  }

  function createNode(tagName, className, text) {
    const node = document.createElement(tagName);
    if (className) {
      node.className = className;
    }
    if (text !== undefined && text !== null) {
      node.textContent = String(text);
    }
    return node;
  }

  function clearNode(node) {
    if (!node) {
      return;
    }
    while (node.firstChild) {
      node.removeChild(node.firstChild);
    }
  }

  function replaceWithEmpty(node, message) {
    if (!node) {
      return;
    }
    clearNode(node);
    node.appendChild(createNode("p", "nm-empty", message));
  }

  function buildMetricCard(label, value, note) {
    const card = createNode("article", "nm-summary-card");
    card.appendChild(createNode("p", "nm-card-kicker", label));
    card.appendChild(createNode("strong", "", value));
    card.appendChild(createNode("small", "", note));
    return card;
  }

  function buildStatusChip(text, tone) {
    const chip = createNode("span", "nm-chip", text);
    chip.dataset.tone = tone || "ok";
    return chip;
  }

  function setText(id, value) {
    const node = byId(id);
    if (node) {
      node.textContent = value;
    }
  }

  function setTone(node, tone) {
    if (!node) {
      return;
    }
    node.classList.remove("nm-tone-ok", "nm-tone-watch", "nm-tone-warn", "nm-tone-danger");
    node.classList.add("nm-tone-" + (tone || "ok"));
  }

  function csvEscape(value) {
    const text = String(value == null ? "" : value);
    if (/[",\n]/.test(text)) {
      return '"' + text.replace(/"/g, '""') + '"';
    }
    return text;
  }

  function downloadCsv(name, rows) {
    if (!rows || !rows.length) {
      return;
    }
    const csv = rows.map(function (row) {
      return row.map(csvEscape).join(",");
    }).join("\n");
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = createNode("a");
    link.href = url;
    link.download = name;
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.setTimeout(function () {
      URL.revokeObjectURL(url);
    }, 0);
  }

  function renderHero(payload) {
    const anomaly = payload.anomaly || {};
    const tone = anomaly.tone || toneFromRisk(anomaly.risk);
    const posture = humanize(anomaly.posture || payload.summary?.posture || payload.portfolio?.posture || "clear");
    const postureNode = byId("nm-posture-pill");
    const ringNode = byId("nm-score-ring");
    const signalsNode = byId("nm-top-signals");

    if (postureNode) {
      postureNode.textContent = posture;
      setTone(postureNode, tone);
    }
    if (ringNode) {
      ringNode.style.setProperty("--score-angle", String(Math.max(0, Math.min(1, valueAsNumber(anomaly.risk))) * 360) + "deg");
    }
    setText("nm-score-value", formatScore(anomaly.risk));
    setText("nm-confidence-value", formatPercent(anomaly.confidence, 0));
    setText("nm-score-note", anomaly.summary || "Activity remains inside the learned billing envelope.");
    setText("nm-generated-at", formatDate(payload.generated_at, true));

    if (payload.scope === "customer") {
      const summary = payload.summary || {};
      setText(
        "nm-dashboard-summary",
        "Balance " + formatTokens(summary.balance_tokens) + " with " + formatTokens(summary.debit_tokens_30d) + " used in the recent 30-day billing window."
      );
    } else {
      const portfolio = payload.portfolio || {};
      setText(
        "nm-dashboard-summary",
        formatCount(portfolio.observed_accounts) + " active accounts observed in the chain window with " + formatCount(portfolio.anomalies_open) + " review-grade anomalies open."
      );
    }

    if (!signalsNode) {
      return;
    }
    clearNode(signalsNode);
    const topSignals = Array.isArray(anomaly.signals) ? anomaly.signals.slice(0, 3) : [];
    if (!topSignals.length) {
      signalsNode.appendChild(buildStatusChip("No elevated signals", "ok"));
      return;
    }
    topSignals.forEach(function (signal) {
      const signalTone = signal.triggered ? toneFromRisk(signal.score) : "ok";
      signalsNode.appendChild(buildStatusChip(signal.label + " " + signal.value, signalTone));
    });
  }

  function renderSummaryCards(payload) {
    const node = byId("nm-summary-cards");
    if (!node) {
      return;
    }
    clearNode(node);
    const cards = [];
    if (payload.scope === "customer") {
      const summary = payload.summary || {};
      const forecast = summary.forecast || {};
      cards.push(buildMetricCard("Balance", formatTokens(summary.balance_tokens), "Paid " + formatTokens(summary.paid_balance_tokens) + " / free " + formatTokens(summary.free_balance_tokens)));
      cards.push(buildMetricCard("Available", formatTokens(summary.available_tokens), "Reserved " + formatTokens(summary.reserved_tokens)));
      cards.push(buildMetricCard("30d usage", formatTokens(summary.debit_tokens_30d), "Lifetime spent " + formatTokens(summary.spent_total_tokens)));
      cards.push(buildMetricCard("30d settlement", formatMoney(summary.payment_minor_30d), "Top-ups " + formatTokens(summary.topup_tokens_30d)));
      cards.push(buildMetricCard("Cash-out total", formatTokens(summary.cashout_total_tokens), "Free grants " + formatTokens(summary.free_grant_total_tokens)));
      cards.push(buildMetricCard("Month forecast", formatMoney(forecast.projected_payment_minor), "Projected usage " + formatTokens(forecast.projected_debit_tokens)));
    } else {
      const portfolio = payload.portfolio || {};
      const chain = payload.chain || {};
      const forecast = portfolio.forecast || {};
      cards.push(buildMetricCard("Observed accounts", formatCount(portfolio.observed_accounts), "Window " + formatCount(portfolio.window_blocks) + " blocks"));
      cards.push(buildMetricCard("Recent usage", formatTokens(portfolio.recent_debit_tokens), "Top-ups " + formatTokens(portfolio.recent_topup_tokens)));
      cards.push(buildMetricCard("Recent settlements", formatMoney(portfolio.recent_payment_minor), "Cash-outs " + formatTokens(portfolio.recent_cashout_tokens)));
      cards.push(buildMetricCard("Open anomalies", formatCount(portfolio.anomalies_open), "Portfolio posture " + humanize(portfolio.posture || "clear")));
      cards.push(buildMetricCard("Chain height", formatCount(chain.height), "Tracked accounts " + formatCount(chain.account_count)));
      cards.push(buildMetricCard("Month forecast", formatMoney(forecast.projected_payment_minor), "Projected usage " + formatTokens(forecast.projected_debit_tokens)));
    }
    cards.forEach(function (card) {
      node.appendChild(card);
    });
  }

  function buildPath(series, width, height, maxValue) {
    const left = 20;
    const right = 20;
    const top = 18;
    const bottom = 24;
    const usableWidth = width - left - right;
    const usableHeight = height - top - bottom;
    return series.map(function (point, index) {
      const x = left + (series.length <= 1 ? usableWidth / 2 : (index / (series.length - 1)) * usableWidth);
      const y = top + usableHeight - (point / maxValue) * usableHeight;
      return (index === 0 ? "M" : "L") + x.toFixed(1) + " " + y.toFixed(1);
    }).join(" ");
  }

  function renderTrend(daily) {
    const chart = byId("nm-trend-chart");
    const legend = byId("nm-trend-legend");
    const forecastCopy = byId("nm-forecast-copy");
    if (!chart || !legend) {
      return;
    }
    clearNode(chart);
    clearNode(legend);
    if (!Array.isArray(daily) || !daily.length) {
      replaceWithEmpty(legend, "No daily ledger points available.");
      if (forecastCopy) {
        forecastCopy.textContent = "Forecast unavailable";
      }
      return;
    }

    const width = 760;
    const height = 280;
    const seriesDefs = [
      { key: "topup_tokens", label: "Top-ups", color: "#f57039" },
      { key: "debit_tokens", label: "Usage", color: "#57d0d1" },
      { key: "cashout_tokens", label: "Cash-outs", color: "#9ee380" },
    ];
    const maxValue = Math.max(1, ...daily.flatMap(function (item) {
      return seriesDefs.map(function (series) {
        return valueAsNumber(item[series.key]);
      });
    }));

    seriesDefs.forEach(function (series) {
      const values = daily.map(function (item) {
        return valueAsNumber(item[series.key]);
      });
      const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
      path.setAttribute("d", buildPath(values, width, height, maxValue));
      path.setAttribute("fill", "none");
      path.setAttribute("stroke", series.color);
      path.setAttribute("stroke-width", "3");
      path.setAttribute("stroke-linecap", "round");
      path.setAttribute("stroke-linejoin", "round");
      chart.appendChild(path);
      const total = values.reduce(function (sum, item) {
        return sum + item;
      }, 0);
      legend.appendChild(buildStatusChip(series.label + " " + formatTokens(total), toneFromRisk(total / maxValue / Math.max(values.length, 1))));
    });

    const axis = document.createElementNS("http://www.w3.org/2000/svg", "line");
    axis.setAttribute("x1", "18");
    axis.setAttribute("x2", "742");
    axis.setAttribute("y1", "256");
    axis.setAttribute("y2", "256");
    axis.setAttribute("stroke", "rgba(255,255,255,0.10)");
    axis.setAttribute("stroke-width", "1");
    chart.appendChild(axis);

    [0, Math.floor(daily.length / 2), daily.length - 1].forEach(function (index) {
      if (index < 0 || index >= daily.length) {
        return;
      }
      const label = document.createElementNS("http://www.w3.org/2000/svg", "text");
      const x = 20 + (daily.length <= 1 ? 360 : (index / (daily.length - 1)) * 720);
      label.setAttribute("x", String(x));
      label.setAttribute("y", "274");
      label.setAttribute("fill", "rgba(237,243,246,0.68)");
      label.setAttribute("font-size", "12");
      label.setAttribute("text-anchor", index === 0 ? "start" : index === daily.length - 1 ? "end" : "middle");
      label.textContent = formatDate(daily[index].date, false).split(" ").slice(0, 2).join(" ");
      chart.appendChild(label);
    });

    const totalTopups = daily.reduce(function (sum, item) {
      return sum + valueAsNumber(item.topup_tokens);
    }, 0);
    const totalUsage = daily.reduce(function (sum, item) {
      return sum + valueAsNumber(item.debit_tokens);
    }, 0);
    if (forecastCopy) {
      forecastCopy.textContent = "Top-ups " + formatTokens(totalTopups) + " vs usage " + formatTokens(totalUsage) + " in the visible window.";
    }
  }

  function renderBreakdown(nodeId, rows, formatter) {
    const node = byId(nodeId);
    if (!node) {
      return;
    }
    clearNode(node);
    if (!Array.isArray(rows) || !rows.length) {
      replaceWithEmpty(node, "No recent activity available.");
      return;
    }
    rows.forEach(function (row) {
      const item = createNode("article", "nm-breakdown-item");
      item.appendChild(createNode("h4", "", formatter.title(row)));
      item.appendChild(createNode("p", "nm-panel-note", formatter.meta(row)));
      const bar = createNode("div", "nm-breakdown-bar");
      const fill = createNode("span");
      fill.style.width = formatter.width(row);
      bar.appendChild(fill);
      item.appendChild(bar);
      node.appendChild(item);
    });
  }

  function renderStatements(statements) {
    const node = byId("nm-statements-table");
    if (!node) {
      return;
    }
    clearNode(node);
    if (!Array.isArray(statements) || !statements.length) {
      const row = createNode("tr");
      const cell = createNode("td", "nm-empty", "No statement periods available.");
      cell.colSpan = 5;
      row.appendChild(cell);
      node.appendChild(row);
      return;
    }
    statements.forEach(function (statement) {
      const row = createNode("tr");
      const periodCell = createNode("td");
      periodCell.appendChild(createNode("strong", "", statement.label || statement.period || "-"));
      periodCell.appendChild(createNode("div", "nm-panel-note", humanize(statement.top_service || statement.provider || "ledger window")));
      row.appendChild(periodCell);
      row.appendChild(createNode("td", "", formatTokens(statement.topup_tokens)));
      row.appendChild(createNode("td", "", formatTokens(statement.debit_tokens)));
      row.appendChild(createNode("td", "", formatMoney(statement.payment_minor, statement.currency)));
      const statusCell = createNode("td", "", humanize(statement.status || "settled"));
      setTone(statusCell, toneFromRisk({ settled: 0.12, watch: 0.45, review: 0.7 }[String(statement.status || "settled")] || 0.12));
      row.appendChild(statusCell);
      node.appendChild(row);
    });
  }

  function renderPaymentMethods(methods) {
    const node = byId("nm-payment-methods");
    if (!node) {
      return;
    }
    clearNode(node);
    if (!Array.isArray(methods) || !methods.length) {
      replaceWithEmpty(node, "No recent settlement method activity available.");
      return;
    }
    methods.forEach(function (method) {
      const card = createNode("article", "nm-method-card");
      card.appendChild(createNode("h4", "", humanize((method.provider || "manual") + " / " + (method.payment_method || "unspecified"))));
      card.appendChild(createNode("p", "nm-panel-note", formatCount(method.transactions) + " settlement events"));
      card.appendChild(createNode("strong", "", formatMoney(method.payment_minor, method.currency)));
      card.appendChild(createNode("div", "nm-panel-note", formatTokens(method.tokens)));
      node.appendChild(card);
    });
  }

  function renderTransactions(transactions) {
    const node = byId("nm-transactions-table");
    if (!node) {
      return;
    }
    clearNode(node);
    if (!Array.isArray(transactions) || !transactions.length) {
      const row = createNode("tr");
      const cell = createNode("td", "nm-empty", "No transactions available.");
      cell.colSpan = 6;
      row.appendChild(cell);
      node.appendChild(row);
      return;
    }
    transactions.forEach(function (transaction) {
      const row = createNode("tr");
      row.appendChild(createNode("td", "", formatDate(transaction.ts, true)));
      const activityCell = createNode("td");
      activityCell.appendChild(createNode("strong", "", transaction.title || humanize(transaction.entry_type)));
      activityCell.appendChild(createNode("div", "nm-panel-note", transaction.subtitle || humanize(transaction.reference || "ledger entry")));
      row.appendChild(activityCell);
      row.appendChild(createNode("td", "", transaction.service_label || "NeuralMimicry ledger activity"));
      row.appendChild(createNode("td", "", formatTokens(transaction.delta_tokens)));
      row.appendChild(createNode("td", "", transaction.payment_minor ? formatMoney(transaction.payment_minor, transaction.currency) : "-"));
      const statusCell = createNode("td", "", humanize(transaction.status || "posted"));
      setTone(statusCell, toneFromRisk(transaction.shortfall_tokens ? 0.8 : transaction.entry_type === "cashout" ? 0.68 : transaction.entry_type === "debit" ? 0.34 : 0.16));
      row.appendChild(statusCell);
      node.appendChild(row);
    });
  }

  function renderSignalMatrix(anomaly) {
    const node = byId("nm-signal-matrix");
    if (!node) {
      return;
    }
    clearNode(node);
    const signals = Array.isArray(anomaly.signals) ? anomaly.signals : [];
    if (!signals.length) {
      replaceWithEmpty(node, "No anomaly signals available.");
      return;
    }
    signals.forEach(function (signal) {
      const card = createNode("article", "nm-signal-card");
      card.appendChild(createNode("p", "nm-panel-label", signal.label));
      card.appendChild(createNode("strong", "", String(signal.value)));
      const meta = createNode("span", "nm-panel-note", "Risk score " + formatScore(signal.score));
      setTone(meta, signal.triggered ? toneFromRisk(signal.score) : "ok");
      card.appendChild(meta);
      node.appendChild(card);
    });
    setText("nm-fuzzy-risk", formatScore(anomaly.fuzzy?.risk));
    setText(
      "nm-fuzzy-meta",
      "Type-" + String(anomaly.fuzzy?.order || 0) + " envelope, confidence " + formatPercent(anomaly.fuzzy?.confidence, 0)
    );
    setText("nm-ai-risk", formatScore(anomaly.ai?.drift_score));
    setText(
      "nm-ai-meta",
      "Neuromimic pulse " + formatScore(anomaly.ai?.risk) + " with state [" + (Array.isArray(anomaly.ai?.state_vector) ? anomaly.ai.state_vector.join(", ") : "0, 0, 0") + "]"
    );
  }

  function renderRecommendations(payload) {
    const recommendationsNode = byId("nm-recommendations");
    const actionsNode = byId("nm-inline-actions");
    if (recommendationsNode) {
      clearNode(recommendationsNode);
      const recommendations = Array.isArray(payload.recommendations) ? payload.recommendations : [];
      if (!recommendations.length) {
        replaceWithEmpty(recommendationsNode, "No operator recommendations available.");
      } else {
        recommendations.forEach(function (recommendation) {
          const item = createNode("article", "nm-recommendation");
          item.dataset.severity = recommendation.severity || "ok";
          item.appendChild(createNode("h4", "", recommendation.title || "Recommendation"));
          item.appendChild(createNode("p", "nm-panel-note", recommendation.detail || "-"));
          recommendationsNode.appendChild(item);
        });
      }
    }
    if (actionsNode) {
      clearNode(actionsNode);
      const actions = Array.isArray(payload.anomaly?.recommended_actions) ? payload.anomaly.recommended_actions : [];
      if (!actions.length) {
        actionsNode.appendChild(buildStatusChip("Continue observation", "ok"));
      } else {
        actions.forEach(function (action) {
          actionsNode.appendChild(buildStatusChip(action, toneFromRisk(payload.anomaly?.risk)));
        });
      }
    }
  }

  function renderPortfolio(payload) {
    if (payload.scope !== "admin") {
      return;
    }
    const portfolioCards = byId("nm-portfolio-cards");
    const providerNode = byId("nm-provider-breakdown");
    const accountsNode = byId("nm-top-accounts-table");
    const queueNode = byId("nm-anomaly-queue");
    const portfolioMeta = byId("nm-portfolio-meta");
    const portfolio = payload.portfolio || {};

    if (portfolioMeta) {
      portfolioMeta.textContent = "Observed " + formatCount(portfolio.observed_accounts) + " accounts across " + formatCount(portfolio.window_blocks) + " recent blocks.";
    }

    if (portfolioCards) {
      clearNode(portfolioCards);
      [
        buildMetricCard("Top-ups", formatTokens(portfolio.recent_topup_tokens), "Recent grants " + formatTokens(portfolio.recent_grant_tokens)),
        buildMetricCard("Usage", formatTokens(portfolio.recent_debit_tokens), "Cash-outs " + formatTokens(portfolio.recent_cashout_tokens)),
        buildMetricCard("Settlement", formatMoney(portfolio.recent_payment_minor), "BTC rate " + String(portfolio.btc_rate || "-")),
        buildMetricCard("Portfolio risk", formatScore(portfolio.risk), "Posture " + humanize(portfolio.posture || "clear")),
      ].forEach(function (card) {
        portfolioCards.appendChild(card);
      });
    }

    renderBreakdown("nm-provider-breakdown", payload.provider_breakdown || [], {
      title: function (row) {
        return humanize(row.provider || "unclassified");
      },
      meta: function (row) {
        return formatMoney(row.payment_minor) + " across " + formatCount(row.payments) + " settlements";
      },
      width: function (row) {
        const total = valueAsNumber(payload.portfolio?.recent_payment_minor) || 1;
        return Math.max(6, Math.round((valueAsNumber(row.payment_minor) / total) * 100)) + "%";
      },
    });

    if (accountsNode) {
      clearNode(accountsNode);
      const accounts = Array.isArray(payload.top_accounts) ? payload.top_accounts : [];
      if (!accounts.length) {
        const row = createNode("tr");
        const cell = createNode("td", "nm-empty", "No account activity available.");
        cell.colSpan = 5;
        row.appendChild(cell);
        accountsNode.appendChild(row);
      } else {
        accounts.forEach(function (account) {
          const row = createNode("tr");
          const accountCell = createNode("td");
          accountCell.appendChild(createNode("strong", "", account.account_ref || account.account_id || "-"));
          accountCell.appendChild(createNode("div", "nm-panel-note", humanize(account.scope || "user")));
          row.appendChild(accountCell);
          row.appendChild(createNode("td", "", formatTokens(account.movement_tokens)));
          row.appendChild(createNode("td", "", formatTokens(account.balance_tokens)));
          const riskCell = createNode("td", "", formatScore(account.risk));
          setTone(riskCell, toneFromRisk(account.risk));
          row.appendChild(riskCell);
          const postureCell = createNode("td", "", humanize(account.posture || account.status || "clear"));
          setTone(postureCell, toneFromRisk(account.risk));
          row.appendChild(postureCell);
          accountsNode.appendChild(row);
        });
      }
    }

    if (queueNode) {
      clearNode(queueNode);
      const queue = Array.isArray(payload.anomaly_queue) ? payload.anomaly_queue : [];
      if (!queue.length) {
        replaceWithEmpty(queueNode, "No accounts currently require operator review.");
      } else {
        queue.forEach(function (account) {
          const item = createNode("article", "nm-queue-item");
          item.appendChild(createNode("h4", "", account.account_ref || account.account_id || "Account"));
          item.appendChild(createNode("p", "nm-panel-note", account.summary || "Review required."));
          item.appendChild(createNode("strong", "", "Risk " + formatScore(account.risk) + " / confidence " + formatPercent(account.confidence, 0)));
          const actions = createNode("div", "nm-inline-actions");
          const recommendedActions = Array.isArray(account.recommended_actions) ? account.recommended_actions.slice(0, 3) : [];
          recommendedActions.forEach(function (action) {
            actions.appendChild(buildStatusChip(action, toneFromRisk(account.risk)));
          });
          item.appendChild(actions);
          queueNode.appendChild(item);
        });
      }
    }
  }

  function renderDashboard(payload) {
    renderHero(payload);
    renderSummaryCards(payload);
    renderTrend(payload.daily || []);
    renderBreakdown("nm-service-breakdown", payload.service_breakdown || [], {
      title: function (row) {
        return row.label || "Service";
      },
      meta: function (row) {
        return formatTokens(row.tokens) + " across " + formatCount(row.events) + " events";
      },
      width: function (row) {
        return Math.max(6, Math.round(valueAsNumber(row.share) * 100)) + "%";
      },
    });
    renderStatements(payload.statements || []);
    renderPaymentMethods(payload.payment_methods || []);
    renderTransactions(payload.transactions || []);
    renderSignalMatrix(payload.anomaly || {});
    renderRecommendations(payload);
    renderPortfolio(payload);
  }

  function renderError(message) {
    setText("nm-dashboard-summary", String(message || "Unable to load billing telemetry."));
    setText("nm-generated-at", "Unavailable");
    setText("nm-score-value", "0.00");
    setText("nm-confidence-value", "0%");
    setText("nm-score-note", "Refresh the page once the upstream ledger and auth services are reachable.");
    replaceWithEmpty(byId("nm-summary-cards"), "Dashboard data unavailable.");
    replaceWithEmpty(byId("nm-trend-legend"), "Dashboard data unavailable.");
    replaceWithEmpty(byId("nm-service-breakdown"), "Dashboard data unavailable.");
    replaceWithEmpty(byId("nm-payment-methods"), "Dashboard data unavailable.");
    replaceWithEmpty(byId("nm-recommendations"), "Dashboard data unavailable.");
    replaceWithEmpty(byId("nm-inline-actions"), "Dashboard data unavailable.");
    replaceWithEmpty(byId("nm-signal-matrix"), "Dashboard data unavailable.");
    replaceWithEmpty(byId("nm-provider-breakdown"), "Dashboard data unavailable.");
    replaceWithEmpty(byId("nm-portfolio-cards"), "Dashboard data unavailable.");
    replaceWithEmpty(byId("nm-anomaly-queue"), "Dashboard data unavailable.");
    const statementsNode = byId("nm-statements-table");
    if (statementsNode) {
      clearNode(statementsNode);
    }
    const transactionsNode = byId("nm-transactions-table");
    if (transactionsNode) {
      clearNode(transactionsNode);
    }
    const accountsNode = byId("nm-top-accounts-table");
    if (accountsNode) {
      clearNode(accountsNode);
    }
  }

  async function loadDashboard(options) {
    if (state.loading) {
      return;
    }
    const refreshButton = byId("nm-refresh");
    state.loading = true;
    if (refreshButton) {
      refreshButton.disabled = true;
      refreshButton.textContent = "Refreshing";
    }
    try {
      const response = await fetch(config.endpoints?.data || "/api/billing/dashboard/customer", {
        credentials: "same-origin",
        headers: { Accept: "application/json" },
      });
      if (response.status === 401) {
        window.location.href = "/login?next=" + encodeURIComponent(window.location.pathname + window.location.search);
        return;
      }
      if (!response.ok) {
        let details = "Unable to load billing telemetry.";
        try {
          const errorPayload = await response.json();
          details = errorPayload.error || details;
        } catch (_error) {
          details = response.statusText || details;
        }
        throw new Error(details);
      }
      state.payload = await response.json();
      renderDashboard(state.payload);
    } catch (error) {
      renderError(error && error.message ? error.message : "Unable to load billing telemetry.");
    } finally {
      state.loading = false;
      if (refreshButton) {
        refreshButton.disabled = false;
        refreshButton.textContent = "Refresh";
      }
    }
  }

  function bindNavigation() {
    document.querySelectorAll(".nm-nav-link").forEach(function (button) {
      button.addEventListener("click", function () {
        document.querySelectorAll(".nm-nav-link").forEach(function (item) {
          item.classList.remove("is-active");
        });
        button.classList.add("is-active");
        const targetId = button.getAttribute("data-target");
        const target = targetId ? document.getElementById(targetId) : null;
        if (target) {
          target.scrollIntoView({ behavior: "smooth", block: "start" });
        }
      });
    });
  }

  function bindExports() {
    const exportTransactions = byId("nm-export-transactions");
    if (exportTransactions) {
      exportTransactions.addEventListener("click", function () {
        const transactions = Array.isArray(state.payload?.transactions) ? state.payload.transactions : [];
        const rows = [["When", "Activity", "Service", "Tokens", "Settlement", "Status", "Reference"]];
        transactions.forEach(function (transaction) {
          rows.push([
            transaction.ts || "",
            transaction.title || humanize(transaction.entry_type),
            transaction.service_label || "",
            String(transaction.delta_tokens || 0),
            transaction.payment_minor ? formatMoney(transaction.payment_minor, transaction.currency) : "",
            transaction.status || "",
            transaction.reference || "",
          ]);
        });
        downloadCsv((config.export_prefix || "neuralmimicry-billing") + "-transactions.csv", rows);
      });
    }

    const exportStatements = byId("nm-export-statements");
    if (exportStatements) {
      exportStatements.addEventListener("click", function () {
        const statements = Array.isArray(state.payload?.statements) ? state.payload.statements : [];
        const rows = [["Period", "Top-ups", "Usage", "Settlement", "Status", "Provider", "Top service"]];
        statements.forEach(function (statement) {
          rows.push([
            statement.label || statement.period || "",
            String(statement.topup_tokens || 0),
            String(statement.debit_tokens || 0),
            formatMoney(statement.payment_minor, statement.currency),
            statement.status || "",
            statement.provider || "",
            statement.top_service || "",
          ]);
        });
        downloadCsv((config.export_prefix || "neuralmimicry-billing") + "-statements.csv", rows);
      });
    }
  }

  function bindRefresh() {
    const refreshButton = byId("nm-refresh");
    if (refreshButton) {
      refreshButton.addEventListener("click", function () {
        loadDashboard({ manual: true });
      });
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    bindNavigation();
    bindRefresh();
    bindExports();
    loadDashboard({ initial: true });
  });
})();
