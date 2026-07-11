/**
 * CSInterface.js — Lightweight wrapper for Adobe's CEP runtime.
 *
 * Provides evalScript (call ExtendScript), getSystemPath, and event helpers.
 * This is a slim version — sufficient for Samosa's needs.
 */

function CSInterface() {
    this.hostEnvironment = window.__adobe_cep__
        ? JSON.parse(window.__adobe_cep__.getHostEnvironment())
        : {};
}

/**
 * Evaluate an ExtendScript expression in the host application.
 * @param {string} script - The ExtendScript code to evaluate.
 * @param {function} [callback] - Optional callback receiving the result string.
 */
CSInterface.prototype.evalScript = function (script, callback) {
    if (window.__adobe_cep__) {
        if (callback) {
            window.__adobe_cep__.evalScript(script, callback);
        } else {
            window.__adobe_cep__.evalScript(script);
        }
    } else {
        // Running outside AE (debug in browser)
        console.warn("[CSInterface] Not in CEP environment. Script:", script);
        if (callback) callback("CEP_NOT_AVAILABLE");
    }
};

/**
 * Get a system path.
 * @param {string} pathType - One of: SystemPath.EXTENSION, SystemPath.USER_DATA, etc.
 * @returns {string}
 */
CSInterface.prototype.getSystemPath = function (pathType) {
    if (window.__adobe_cep__) {
        return window.__adobe_cep__.getSystemPath(pathType);
    }
    return "";
};

/**
 * Open a URL in the default browser.
 * @param {string} url
 */
CSInterface.prototype.openURLInDefaultBrowser = function (url) {
    if (window.__adobe_cep__) {
        window.__adobe_cep__.openURLInDefaultBrowser(url);
    } else {
        window.open(url, "_blank");
    }
};

/**
 * System path type constants.
 */
var SystemPath = {
    EXTENSION: "extension",
    USER_DATA: "userData",
    COMMON_FILES: "commonFiles",
    MY_DOCUMENTS: "myDocuments",
    APPLICATION: "application",
    HOST_APPLICATION: "hostApplication",
};
