#include "json2.jsx"

(function () {
    if (!$._samosaAE) {
        $._samosaAE = {};
    }

    function result(value) {
        return JSON.stringify(value);
    }

    function activeComp() {
        var item = app.project ? app.project.activeItem : null;
        return item && item instanceof CompItem ? item : null;
    }

    function selectedFootageLayer() {
        var comp = activeComp();
        if (!comp || !comp.selectedLayers || comp.selectedLayers.length !== 1) {
            return null;
        }
        var layer = comp.selectedLayers[0];
        try {
            if (layer instanceof AVLayer && layer.source instanceof FootageItem && layer.source.mainSource instanceof FileSource) {
                return layer;
            }
        } catch (e) {
        }
        return null;
    }

    function copyProperty(source, target) {
        if (!source || !target || target.isReadOnly) {
            return;
        }
        try {
            if (source.numKeys > 0) {
                for (var k = 1; k <= source.numKeys; k++) {
                    target.setValueAtTime(source.keyTime(k), source.keyValue(k));
                    try { target.setInterpolationTypeAtKey(k, source.keyInInterpolationType(k), source.keyOutInterpolationType(k)); } catch (e1) {}
                    try { target.setTemporalEaseAtKey(k, source.keyInTemporalEase(k), source.keyOutTemporalEase(k)); } catch (e2) {}
                    try { target.setTemporalContinuousAtKey(k, source.keyTemporalContinuous(k)); } catch (e3) {}
                    try { target.setTemporalAutoBezierAtKey(k, source.keyTemporalAutoBezier(k)); } catch (e4) {}
                }
            } else {
                target.setValue(source.value);
            }
            if (source.canSetExpression && target.canSetExpression && source.expression) {
                target.expression = source.expression;
                target.expressionEnabled = source.expressionEnabled;
            }
        } catch (e) {
        }
    }

    function copyTransform(sourceLayer, targetLayer) {
        var sourceGroup = sourceLayer.property("ADBE Transform Group");
        var targetGroup = targetLayer.property("ADBE Transform Group");
        if (!sourceGroup || !targetGroup) {
            return;
        }
        var names = ["ADBE Anchor Point", "ADBE Position", "ADBE Position_0", "ADBE Position_1", "ADBE Position_2", "ADBE Scale", "ADBE Orientation", "ADBE Rotate X", "ADBE Rotate Y", "ADBE Rotate Z", "ADBE Opacity"];
        for (var i = 0; i < names.length; i++) {
            copyProperty(sourceGroup.property(names[i]), targetGroup.property(names[i]));
        }
    }

    $._samosaAE.getSelectedLayerInfo = function () {
        try {
            var comp = activeComp();
            if (!comp) {
                return result({ ok: false, error: "Open a composition first." });
            }
            var layer = selectedFootageLayer();
            if (!layer) {
                return result({ ok: false, error: "Select exactly one file-backed footage layer." });
            }
            var file = layer.source.mainSource.file;
            if (!file || !file.exists) {
                return result({ ok: false, error: "The selected footage file is offline." });
            }
            var sourceTime = Math.max(0, (comp.time - layer.startTime) * 100 / layer.stretch);
            return result({
                ok: true,
                path: file.fsName,
                compName: comp.name,
                layerName: layer.name,
                sourceName: layer.source.name,
                layerIndex: layer.index,
                sourceTime: sourceTime,
                startTime: layer.startTime,
                inPoint: layer.inPoint,
                outPoint: layer.outPoint,
                stretch: layer.stretch,
                frameRate: comp.frameRate
            });
        } catch (e) {
            return result({ ok: false, error: e.toString(), line: e.line });
        }
    };

    $._samosaAE.importResult = function (pathValue, isSequence, referenceIndex) {
        app.beginUndoGroup("Samosa: Add Result");
        try {
            var file = new File(pathValue);
            if (!file.exists) {
                return result({ ok: false, error: "Output file does not exist: " + pathValue });
            }
            var comp = activeComp();
            var reference = null;
            if (comp && referenceIndex > 0 && referenceIndex <= comp.numLayers) {
                reference = comp.layer(referenceIndex);
            }
            var options = new ImportOptions(file);
            options.importAs = ImportAsType.FOOTAGE;
            if (isSequence) {
                options.sequence = true;
                options.forceAlphabetical = true;
            }
            var item = app.project.importFile(options);
            item.name = "Samosa - " + item.name;
            try {
                if (item.mainSource instanceof FileSource) {
                    item.mainSource.alphaMode = AlphaMode.STRAIGHT;
                    if (isSequence && comp) {
                        item.mainSource.conformFrameRate = comp.frameRate;
                    }
                }
            } catch (e1) {
            }
            if (!comp) {
                return result({ ok: true, layerName: item.name, projectOnly: true });
            }
            var layer = comp.layers.add(item);
            layer.name = "Samosa - " + item.name;
            layer.label = 13;
            if (reference) {
                layer.moveBefore(reference);
                try { layer.threeDLayer = reference.threeDLayer; } catch (e2) {}
                try { layer.startTime = reference.startTime; } catch (e3) {}
                try { layer.inPoint = reference.inPoint; } catch (e4) {}
                try { layer.outPoint = reference.outPoint; } catch (e5) {}
                try { layer.stretch = reference.stretch; } catch (e6) {}
                copyTransform(reference, layer);
            }
            return result({ ok: true, layerName: layer.name, projectOnly: false });
        } catch (e) {
            return result({ ok: false, error: e.toString(), line: e.line });
        } finally {
            app.endUndoGroup();
        }
    };
})();
