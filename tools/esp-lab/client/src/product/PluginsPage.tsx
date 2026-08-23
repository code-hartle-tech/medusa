import { useCallback, useEffect, useState } from 'react';
import { useLab } from '../lib/lab';

/**
 * Plugins — the extensibility surface.
 *
 * The point of showing a plugin's declared capabilities and its steps *before*
 * anything runs is that the operator should never have to trust a file to find
 * out what it does. The firmware enforces the declaration (a plugin that did
 * not declare tx cannot contain a tx step), so what is rendered here is not a
 * summary someone wrote — it is the contract the device will hold the plugin
 * to.
 */

export type PluginSummary = {
  file: string;
  source: 'bundled' | 'saved';
  id: string;
  name: string;
  author: string;
  needs: string;
  steps: number;
};

const CAPABILITY_COPY: Record<string, string> = {
  rx: 'Listens to nearby radio traffic',
  tx: 'Transmits — subject to your session, scope and rate policy',
  storage: 'Writes findings to local storage',
};

export default function PluginsPage() {
  const lab = useLab();
  const [plugins, setPlugins] = useState<PluginSummary[]>([]);
  const [selected, setSelected] = useState<PluginSummary | null>(null);
  const [source, setSource] = useState('');
  const [report, setReport] = useState('');
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(() => {
    lab.pluginList();
    lab.journeyGet();
  }, [lab]);

  useEffect(() => {
    if (lab.connected) refresh();
  }, [lab.connected, refresh]);

  useEffect(() => {
    setPlugins(lab.plugins);
  }, [lab.plugins]);

  const open = (plugin: PluginSummary) => {
    setSelected(plugin);
    setReport('');
    setBusy(true);
    lab.pluginRead(plugin.file, plugin.source, (text) => {
      setSource(text);
      setBusy(false);
    });
  };

  const dryRun = () => {
    if (!source) return;
    setBusy(true);
    lab.pluginValidate(source, (result) => {
      setReport(result);
      setBusy(false);
    });
  };

  const journey = lab.journey;

  return (
    <div className="mp-page-stack">
      {journey && (
        <section className="mp-panel">
          <header className="mp-panel-head">
            <span className="mp-eyebrow">Guided path</span>
            <h2>{journey.title}</h2>
            <p>{journey.summary}</p>
          </header>

          <ol className="mp-journey">
            {journey.steps.map((step, index) => (
              <li key={step.id} className="mp-journey-step">
                <span className="mp-journey-index" aria-hidden="true">{index + 1}</span>
                <div className="mp-journey-body">
                  <strong>{step.title}</strong>
                  <p className="mp-journey-goal">{step.goal}</p>
                  {/* The reason is the teaching. A step that only says what to
                      click teaches clicking. */}
                  <p className="mp-journey-why">{step.why}</p>
                  <p className="mp-journey-look">
                    <span>Look for</span> {step.look_for}
                  </p>
                  {step.plugin && (
                    <button
                      type="button"
                      className="mp-button mp-button-quiet"
                      onClick={() => {
                        const match = plugins.find((candidate) => candidate.file === step.plugin);
                        if (match) open(match);
                      }}
                    >
                      Open {step.plugin.replace('.medusa', '')}
                    </button>
                  )}
                </div>
              </li>
            ))}
          </ol>
        </section>
      )}

      <section className="mp-panel">
        <header className="mp-panel-head">
          <span className="mp-eyebrow">Installed</span>
          <h2>{plugins.length} available</h2>
          <p>
            A plugin cannot reach memory or hardware directly, and anything it transmits passes
            the firmware guard exactly as a built-in operation would. What each one declares
            below is what the device will hold it to.
          </p>
        </header>

        {!lab.connected && (
          <p className="mp-empty-note">Connect the local bridge to list plugins.</p>
        )}

        {lab.connected && plugins.length === 0 && (
          <p className="mp-empty-note">No plugins found. The devkit can scaffold one in a single command.</p>
        )}

        <div className="mp-plugin-grid">
          {plugins.map((plugin) => (
            <article
              key={`${plugin.source}:${plugin.file}`}
              className={`mp-plugin-card ${selected?.file === plugin.file ? 'is-open' : ''}`}
            >
              <div className="mp-plugin-top">
                <strong>{plugin.name || plugin.id}</strong>
                <span className={`mp-plugin-origin is-${plugin.source}`}>{plugin.source}</span>
              </div>
              <small className="mp-plugin-author">{plugin.author || 'anonymous'}</small>

              {/* Declared capabilities, shown before anything runs. */}
              <ul className="mp-plugin-caps">
                {plugin.needs
                  .split(',')
                  .map((c) => c.trim())
                  .filter(Boolean)
                  .map((cap) => (
                    <li key={cap} className={cap === 'tx' ? 'is-tx' : undefined}>
                      <strong>{cap}</strong>
                      <small>{CAPABILITY_COPY[cap] ?? 'Unrecognised capability'}</small>
                    </li>
                  ))}
              </ul>

              <div className="mp-plugin-foot">
                <small>{plugin.steps} steps</small>
                <button type="button" className="mp-button mp-button-quiet" onClick={() => open(plugin)}>
                  Inspect
                </button>
              </div>
            </article>
          ))}
        </div>
      </section>

      {selected && (
        <section className="mp-panel">
          <header className="mp-panel-head">
            <span className="mp-eyebrow">{selected.file}</span>
            <h2>{selected.name || selected.id}</h2>
            <p>
              Read the steps, then dry-run to see exactly what it would do. A dry run executes the
              program against a simulated device: nothing is transmitted and no radio is touched.
            </p>
          </header>

          <pre className="mp-plugin-source" aria-label="Plugin source">{source}</pre>

          <div className="mp-row-actions">
            <button type="button" className="mp-button mp-button-primary" disabled={busy || !source} onClick={dryRun}>
              {busy ? 'Working…' : 'Dry run'}
            </button>
          </div>

          {report && (
            <>
              <h3 className="mp-plugin-report-head">What it would do</h3>
              <pre className="mp-plugin-source" aria-label="Dry run result">{report}</pre>
            </>
          )}
        </section>
      )}
    </div>
  );
}
