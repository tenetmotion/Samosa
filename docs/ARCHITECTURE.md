# Architecture

Samosa separates host integration from GPU processing so After Effects remains responsive and model dependencies stay in the existing Sammie-Roto-2 environment.

```text
After Effects
  -> CEP panel (HTML/CSS/JavaScript)
  -> ExtendScript host bridge
  -> localhost HTTP service (127.0.0.1)
      -> pinned Sammie-Roto-2 environment (SAM 2.1, matting, removal)
      -> isolated SAM 3.1 worker environment (optional Windows NVIDIA path)
      -> isolated CorridorKey 2.0 CLI environment (optional Windows NVIDIA path)
  -> local, separately licensed checkpoints
```

## CEP panel

The panel owns interaction state, prompt placement, object selection, workflow tabs, viewer rendering, progress polling, cancellation, and service startup. Each panel registers a unique client token and CEP process ID; the service exits only after every registered panel releases or disappears. Its generated `config.json` contains local service and runtime paths only. Hugging Face credentials are never persisted there.

## Host bridge

`panel/host/host.jsx` reads the selected file-backed layer, translates its comp-time In/Out points to a half-open source interval, and reports both effective and native footage frame rates. Completed trimmed output begins at the reference layer's In point and preserves positive stretch and transform properties. Time-remapped and reverse-stretched sources are rejected because they do not have one linear source interval. The bridge is ExtendScript ES3 and wraps project mutation in an After Effects undo group.

## Local service

`backend/service.py` binds only to `127.0.0.1`. It adapts the desktop-oriented Sammie-Roto-2 runtime for headless use, seeks to the selected source interval, samples presentation timestamps at After Effects' effective footage cadence, renumbers the decoded trim from frame zero, isolates temporary session data, exposes frames and state, and runs long operations as cancellable background jobs.

## SAM 3.1 worker

SAM 3.1 runs in a second Python environment under `runtime/SAM3.1` because its NumPy constraint conflicts with the pinned Sammie runtime. `backend/sam3_bridge.py` owns a JSON-lines subprocess connection to `backend/sam3_worker.py`; request IDs, timeouts, crash detection, cancellation, clean shutdown, and a parent-process watchdog prevent a detached model process from surviving its service.

The worker uses the official video predictor. Because that predictor accepts JPEG frame directories, the service creates an idempotent, quality-95 JPEG mirror of Samosa's decoded PNG sequence and validates that every expected frame is present before a session starts. Semantic prompts replace the active concept, while point prompts retain that state to refine existing masks or add objects, matching the official SAM 3.1 interaction sequence. Small control messages cross standard input/output, while masks use bounded raw-array files under the session transport directory. The service validates each path, shape, type, and byte count before converting it to the same per-frame PNG mask layout used by existing matting, removal, preview, and export operations. Predictor operations are serialized inside the worker while cancellation remains out-of-band. Setup passes a Hugging Face token only in the child-process environment, redacts output, and discards it after the process exits.

## CorridorKey 2.0 process

CorridorKey 2.0 runs unmodified in `runtime/CorridorKey2.0`. `backend/corridorkey_bridge.py` builds and launches the pinned official CLI, translates stdout phases into the existing job protocol, and terminates it on cancellation. Samosa supplies its decoded frame directory plus either a complete tracked-mask sequence, a first-frame mask, or no hint for automatic keying. The bridge requires an exact alpha and cutout frame count before accepting a result.

Alpha EXRs are normalized into Sammie's per-object matte layout for preview and existing matte outputs. CorridorKey's scene-linear straight-alpha cutout EXRs remain untouched for EXR export. Display-oriented PNG and video paths convert linear RGB to sRGB and preserve alpha. Source, weights, and foundation models remain in the separately licensed runtime and are never copied into Samosa packages.

## Composition-view selection

Direct clicks in the After Effects Composition viewer require a native C++ effect using Adobe's Custom Comp UI APIs. That optional module should translate comp coordinates to source-frame coordinates and call the existing point API. Adobe SDK code and binaries must remain outside the source package unless their distribution terms have been independently verified.

## Dependency boundary

Samosa does not vendor Sammie-Roto-2, SAM 3.1, CorridorKey 2.0 source, or model weights in its repository. Release installers fetch pinned, checksum-verified source revisions and separately licensed checkpoints. Upstream core fixes should be proposed to their source project; Samosa carries compatibility adapters only for headless and After Effects integration.
