(function () {
    var commandId = app.findMenuCommandId("Samosa");
    if (commandId) {
        app.executeCommand(commandId);
    }
})();
