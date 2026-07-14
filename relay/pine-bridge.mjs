// pine-bridge.mjs
// Bridge: connect to TradingView Desktop via CDP (default http://127.0.0.1:9222),
// read a named Pine script's outputs from the active chart, and emit a structured
// JSON "conclusion" that macro-os (Python) consumes.
//
// Reuses the zero-dependency CDP client in ./tv-cdp-client.mjs and the proven
// snapshot expression copied from ./tv-desktop-monitor.mjs. No external npm deps.
//
// Usage:
//   node relay/pine-bridge.mjs [--cdp-url URL] [--symbol SYM] [--script NAME]
//                              [--chart-match MATCH] [--out PATH] [--json] [--dry]
//
// Output: a single JSON line on stdout (PineConclusion shape). Logs go to stderr.

import { writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { listTargets, selectChartTarget, snapshotTarget } from "./tv-cdp-client.mjs";

const DEFAULT_CDP_URL = "http://127.0.0.1:9222";
const RELAY_DIR = dirname(fileURLToPath(import.meta.url));

// ── Logging (stderr only, keep stdout clean for JSON) ──
function log(level, message, details = {}) {
  const ts = new Date().toISOString();
  process.stderr.write(`${JSON.stringify({ ts, level, message, ...details })}\n`);
}

// ── Snapshot expression (verbatim from tv-desktop-monitor.mjs) ──
// Reads every chart widget + every data source (Pine study) on the page and
// simplifies each study's plot values + alert metadata into plain objects.
function snapshotExpression() {
  return `(() => {
    try {
      const collection = window._exposed_chartWidgetCollection;
      const widgets = [];

      if (typeof collection?.activeChartWidget?.value === "function") {
        const active = collection.activeChartWidget.value();
        if (active) widgets.push(active);
      }

      if (widgets.length === 0 && typeof collection?.getAll === "function") {
        const all = collection.getAll();
        if (Array.isArray(all)) widgets.push(...all);
      }

      const page = { href: location.href, title: document.title };
      const plotOrderKey = (key) => {
        const match = String(key ?? "").match(/(\\d+)$/);
        return match ? Number(match[1]) : Number.POSITIVE_INFINITY;
      };
      const simplifyCell = (cell) => ({
        id: String(cell?.id ?? ""),
        index: cell?.index ?? null,
        orderIndex: cell?.orderIndex ?? null,
        title: String(cell?.title ?? ""),
        value: String(cell?.value ?? ""),
        visible: cell?.visible ?? null,
      });
      const simplifyAlertMeta = (source) => {
        const meta = typeof source?._metaInfo?.value === "function" ? source._metaInfo.value() : null;
        if (!meta || typeof meta !== "object") return null;

        const plots = [];
        const styles = meta.styles && typeof meta.styles === "object" ? meta.styles : {};
        for (const [key, style] of Object.entries(styles)
          .sort(([left], [right]) => plotOrderKey(left) - plotOrderKey(right))
          .slice(0, 40)) {
          const title = String(style?.title ?? "");
          const text = String(style?.text ?? "");
          const type = String(style?.plottype ?? "");
          if (!title && !text && !type) continue;
          plots.push({
            id: String(key),
            title,
            text,
          });
        }

        const bands = Array.isArray(meta.bands)
          ? meta.bands.slice(0, 20).map((band, index) => ({
              id: String(band?.id ?? ("band_" + index)),
              name: String(band?.name ?? ""),
              value: band?.value ?? null,
              isHidden: band?.isHidden ?? null,
            }))
          : [];

        return {
          title: String(meta.title ?? ""),
          shortTitle: String(meta.shortTitle ?? ""),
          shortDescription: String(meta.shortDescription ?? ""),
          fullId: String(meta.fullId ?? ""),
          plots,
          bands,
        };
      };
      const simplifyStudy = (source) => {
        const name = typeof source?.name === "function" ? source.name() : "";
        const alertMeta = simplifyAlertMeta(source);

        let values = null;
        try {
          const provider = source?._tableViewValuesProvider;
          const rawValues = typeof provider?.getValues === "function" ? provider.getValues() : null;
          values = Array.isArray(rawValues) ? rawValues.slice(0, 40).map(simplifyCell) : null;
        } catch (error) {
          values = [{ title: "error", value: String(error?.message ?? error), visible: true }];
        }

        const looksEmpty = !Array.isArray(values) || values.every((cell) => {
          const text = String(cell?.value ?? "").trim();
          return !text || text === "∅" || text === "object";
        });
        if (looksEmpty) {
          try {
            const dataWindowItems = Array.isArray(source?._dataWindowView?._items) ? source._dataWindowView._items : [];
            const windowValues = dataWindowItems.slice(0, 40).map((item, index) => ({
              id: String(item?._id ?? ("window_" + index)),
              index,
              orderIndex: index,
              title: String((typeof item?.title === "function" ? item.title() : item?._title) ?? ""),
              value: String((typeof item?.value === "function" ? item.value() : item?._value) ?? ""),
              visible: item?._visible ?? true,
            }));
            if (windowValues.some((cell) => {
              const text = String(cell?.value ?? "").trim();
              return text && text !== "∅" && text !== "object";
            })) {
              values = windowValues;
            }
          } catch (error) {
            // Ignore data window failures and continue to row fallback.
          }
        }
        if (!Array.isArray(values) || values.every((cell) => {
          const text = String(cell?.value ?? "").trim();
          return !text || text === "∅" || text === "object";
        })) {
          try {
            const data = typeof source?.data === "function" ? source.data() : null;
            const items = Array.isArray(data?._items) ? data._items : [];
            const lastRow = items.length > 0 ? items[items.length - 1] : null;
            const rowValues = Array.isArray(lastRow?.value) ? lastRow.value : null;
            if (Array.isArray(rowValues) && rowValues.length > 1) {
              const plotTitles = Array.isArray(alertMeta?.plots)
                ? alertMeta.plots.map((plot, index) => String(plot?.title ?? ("plot_" + index)))
                : [];
              const fallbackLimit = plotTitles.length > 0
                ? Math.min(plotTitles.length, rowValues.length - 1)
                : Math.min(rowValues.length - 1, 24);
              values = rowValues.slice(1, 1 + fallbackLimit).map((value, index) => ({
                id: String("plot_" + index),
                index,
                orderIndex: index,
                title: plotTitles[index] || String("plot_" + index),
                value: String(value ?? ""),
                visible: true,
              }));
            }
          } catch (error) {
            // Ignore fallback failures and keep the provider values.
          }
        }

        return {
          name,
          kind: source?.constructor?.name ?? "",
          state: {
            alertId: String(source?._alertId ?? ""),
            alertStatus: String(source?._alertStatus ?? ""),
            active: source?._active === true,
            visible: source?._visible === true,
            status: String(source?._status ?? ""),
            localFireTime: String(source?._localFireTime ?? ""),
          },
          alertMeta,
          values,
        };
      };

      const charts = widgets.map((widget, index) => {
        const model = typeof widget?.model === "function" ? widget.model() : null;
        const rawSources = typeof model?.orderedDataSources === "function" ? model.orderedDataSources() : [];
        const sources = Array.isArray(rawSources) ? rawSources : [];
        const symbolString = typeof model?.getSymbolString === "function" ? String(model.getSymbolString()) : "";
        const symbolMatch = symbolString.match(/"symbol":"([^"]+)"/);
        const symbol = typeof widget?.getSymbol === "function" && widget.getSymbol() ? widget.getSymbol() : symbolMatch?.[1] ?? "";
        const resolution = typeof widget?.getResolution === "function" && widget.getResolution() ? widget.getResolution() : String(model?.interval ?? "");

        return {
          index,
          title: typeof widget?.title === "function" && widget.title() ? widget.title() : page.title,
          symbol,
          resolution,
          url: location.href,
          studies: sources.slice(0, 40).map(simplifyStudy),
        };
      });

      return JSON.stringify({ page, charts });
    } catch (error) {
      return JSON.stringify({ error: String(error?.message ?? error), charts: [] });
    }
  })()`;
}

// ── Argument parsing ──
function parseArgs(argv) {
  const args = new Map();
  for (let i = 2; i < argv.length; i += 1) {
    const token = argv[i];
    if (!token.startsWith("--")) continue;
    const key = token.slice(2);
    const next = argv[i + 1];
    if (next && !next.startsWith("--")) {
      args.set(key, next);
      i += 1;
    } else {
      args.set(key, "true");
    }
  }
  return args;
}

function readEnv(name) {
  const value = process.env[name];
  return value && value.trim() ? value.trim() : undefined;
}

function isEmptyValue(value) {
  const text = String(value ?? "").trim();
  return !text || text === "∅" || text === "object" || text === "[object Object]" || text === "[]";
}

function toNullableNumber(value) {
  if (value == null || value === "") return null;
  const match = String(value).match(/-?(?:\d+\.?\d*|\.\d+)/);
  if (!match) return null;
  const parsed = Number(match[0]);
  return Number.isFinite(parsed) ? parsed : null;
}

function studyMatches(study, patterns) {
  const name = String(study?.name ?? "");
  return patterns.some((pattern) => pattern.test(name));
}

// Study names that carry no actionable Pine conclusion (mirrors isNoiseStudyName
// in tv-monitor-core.mjs). Excluded from auto-selection.
const NOISE_STUDY_NAMES = new Set([
  "Crosshair",
  "Dividends",
  "Splits",
  "Earnings",
  "Dates calculator for continuous",
  "FuturesContractExpiration",
  "ChartEventsSource",
  "LatestUpdatesSource",
  "Ideas on chart",
  "Moving Average Exponential",
  "Crosshair",
]);

function isNoiseStudy(study) {
  return NOISE_STUDY_NAMES.has(String(study?.name ?? ""));
}

// Pick the most relevant Pine study.
//  1. If --script is given: match by name strictly. Missing -> return null so the
//     caller fails loud (adapter then falls back to mock).
//  2. Else: auto-select the non-noise study with the richest set of populated values
//     (preferring Pine script widgets of kind "Ws"/"Tb" on ties).
function selectPineStudy(chart, { scriptName, symbol }) {
  const studies = Array.isArray(chart?.studies) ? chart.studies : [];
  if (studies.length === 0) return null;

  if (scriptName) {
    const needle = scriptName.toLowerCase();
    const hit = studies.find((study) => String(study?.name ?? "").toLowerCase().includes(needle));
    return hit ?? null;
  }

  // Auto-select: prefer actual Pine script studies (kind Ws/Tb), excluding the
  // plain price series (kind "ns") and noise studies. Fall back to the richest
  // study only if no script study carries values.
  let best = null;
  let bestScore = -1;
  for (const study of studies) {
    if (isNoiseStudy(study)) continue;
    if (String(study?.kind ?? "") === "ns") continue; // main price series, not a Pine conclusion
    const cells = Array.isArray(study?.values) ? study.values : [];
    const score = cells.filter((cell) => cell?.visible !== false && !isEmptyValue(cell?.value)).length;
    const kindBonus = /^(Ws|Tb)$/.test(String(study?.kind ?? "")) ? 0.5 : 0;
    const total = score + kindBonus;
    if (total > bestScore) {
      bestScore = total;
      best = study;
    }
  }

  // If no script study had values, fall back to the richest study outright.
  if (!best) {
    for (const study of studies) {
      const cells = Array.isArray(study?.values) ? study.values : [];
      const score = cells.filter((cell) => cell?.visible !== false && !isEmptyValue(cell?.value)).length;
      if (score > bestScore) {
        bestScore = score;
        best = study;
      }
    }
  }
  return best;
}

// Derive a generic conclusion from a study's plot values / alert plots.
function buildConclusion(chart, study) {
  const values = Array.isArray(study?.values) ? study.values : [];
  const plots = Array.isArray(study?.alertMeta?.plots) ? study.alertMeta.plots : [];

  const populated = values.filter((cell) => cell?.visible !== false && !isEmptyValue(cell?.value));

  const findValueCell = (patterns) =>
    populated.find((cell) => patterns.some((pattern) => pattern.test(String(cell?.title ?? ""))));

  const valueCell =
    findValueCell([/score|value|level|code|%/i]) ||
    populated.find((cell) => toNullableNumber(cell?.value) != null);
  const signalCell = findValueCell([/action|signal|state|regime|decision/i]);
  const confidenceCell = findValueCell([/confidence/i]);

  const value = valueCell ? toNullableNumber(valueCell.value) : null;
  const signal = signalCell ? String(signalCell.value).trim() : null;
  const confidence = confidenceCell ? toNullableNumber(confidenceCell.value) : null;
  const label = signal ?? null;

  return {
    source_script: String(study?.name ?? ""),
    symbol: String(chart?.symbol ?? ""),
    tf: String(chart?.resolution ?? ""),
    chart_title: String(chart?.title ?? ""),
    fetched_at: new Date().toISOString(),
    signal,
    confidence,
    value,
    label,
    payload: {
      study_name: String(study?.name ?? ""),
      study_kind: String(study?.kind ?? ""),
      values: populated.map((cell) => ({
        id: cell?.id ?? "",
        title: cell?.title ?? "",
        value: cell?.value ?? "",
        visible: cell?.visible ?? null,
      })),
      plots: plots.map((plot) => ({
        id: plot?.id ?? "",
        title: plot?.title ?? "",
        text: plot?.text ?? "",
      })),
    },
  };
}

async function main() {
  const args = parseArgs(process.argv);
  const cdpUrl = args.get("cdp-url") ?? readEnv("CDP_URL") ?? DEFAULT_CDP_URL;
  const symbol = args.get("symbol") ?? readEnv("PINE_SYMBOL") ?? "";
  const scriptName = args.get("script") ?? readEnv("PINE_SCRIPT") ?? "";
  const chartMatch = args.get("chart-match") ?? readEnv("CHART_MATCH") ?? "Gold";
  const outPath = args.get("out") ?? readEnv("PINE_OUT_PATH") ?? "";
  const dryRun = args.get("dry") === "true";

  if (dryRun) {
    const conclusion = {
      source_script: scriptName || "DRY_RUN",
      symbol: symbol || "TVC:GOLD",
      tf: "1D",
      chart_title: "dry-run",
      fetched_at: new Date().toISOString(),
      signal: null,
      confidence: null,
      value: null,
      label: null,
      payload: { study_name: "", study_kind: "", values: [], plots: [] },
    };
    process.stdout.write(`${JSON.stringify(conclusion)}\n`);
    return;
  }

  const targets = await listTargets(cdpUrl);
  const target = selectChartTarget(targets, { chartMatch });
  if (!target) {
    throw new Error(`no TradingView chart target found (chartMatch=${chartMatch})`);
  }

  const rawText = await snapshotTarget(target.webSocketDebuggerUrl, snapshotExpression());
  const raw = typeof rawText === "string" ? JSON.parse(rawText) : rawText;
  if (raw?.error) {
    throw new Error(raw.error);
  }

  const charts = Array.isArray(raw?.charts) ? raw.charts : [];
  if (charts.length === 0) {
    throw new Error("no charts found in TradingView target");
  }

  // Prefer a chart whose symbol matches, else the first chart.
  const chart =
    charts.find((c) => symbol && String(c?.symbol ?? "").toLowerCase().includes(symbol.toLowerCase())) ??
    charts[0];

  const study = selectPineStudy(chart, { scriptName, symbol });
  if (!study) {
    throw new Error(
      `no Pine study found on chart "${chart?.title}" (symbol=${chart?.symbol}, scriptName=${scriptName || "—"})`,
    );
  }

  const conclusion = buildConclusion(chart, study);

  if (outPath) {
    await writeFile(outPath, `${JSON.stringify(conclusion, null, 2)}\n`, "utf8");
    log("info", "wrote pine conclusion", { outPath });
  }

  process.stdout.write(`${JSON.stringify(conclusion)}\n`);
}

main().catch((error) => {
  process.stderr.write(`${JSON.stringify({ ts: new Date().toISOString(), level: "error", message: error instanceof Error ? error.message : String(error) })}\n`);
  process.exitCode = 1;
});
