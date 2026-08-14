# Architecture

Samosa separates host integration from GPU processing so After Effects remains responsive and model dependencies stay in the existing Sammie-Roto-2 environment.

```text
After Effects
  -> CEP panel (HTML/CSS/JavaScript)
  -> ExtendScript host bridge
  -> localhost HTTP service (127.0.0.1)
  -> separately installed Sammie-Roto-2 Python runtime
  -> selected model and local checkpoints
```

## CEP panel

The panel owns interaction state, point placement, object selection, workflow tabs, viewer rendering, progress polling, cancellation, and service startup. Each panel registers a unique client token and CEP process ID; the service exits only after every registered panel releases or disappears. Its generated `config.json` contains only the local service port and paths to the user's upstream checkout and Python executable.

## Host bridge

`panel/host/host.jsx` reads the selected file-backed layer, translates its comp-time In/Out points to a half-open source interval, and reports both effective and native footage frame rates. Completed trimmed output begins at the reference layer's In point and preserves positive stretch and transform properties. Time-remapped and reverse-stretched sources are rejected because they do not have one linear source interval. The bridge is ExtendScript ES3 and wraps project mutation in an After Effects undo group.

## Local service

`backend/service.py` binds only to `127.0.0.1`. It adapts the desktop-oriented Sammie-Roto-2 runtime for headless use, seeks to the selected source interval, samples presentation timestamps at After Effects' effective footage cadence, renumbers the decoded trim from frame zero, isolates temporary session data, exposes frames and state, and runs long operations as cancellable background jobs.

## Composition-view selection

Direct clicks in the After Effects Composition viewer require a native C++ effect using Adobe's Custom Comp UI APIs. That optional module should translate comp coordinates to source-frame coordinates and call the existing point API. Adobe SDK code and binaries must remain outside the source package unless their distribution terms have been independently verified.

## Dependency boundary

Samosa does not vendor Sammie-Roto-2. Upstream core fixes should be proposed to that project. Samosa should carry a compatibility adapter only when the behavior is specific to headless or After Effects integration.
