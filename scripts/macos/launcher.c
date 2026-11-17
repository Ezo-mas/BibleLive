/*
 * BibleLive.app/Contents/MacOS/BibleLive: the app's own executable, embedding the bundled Python.
 *
 * Why a compiled launcher and not a shell script that execs python3: macOS (TCC) gives
 * microphone access to the "responsible" app and takes its identity and the prompt text
 * (NSMicrophoneUsageDescription) from that app's bundle. The process must therefore be
 * the bundle's CFBundleExecutable. After a shell script execs python3.13, the process is
 * the Python binary with no Info.plist of its own, so the prompt could name "python3.13"
 * or never appear. This binary stays the process and runs the launcher in-process;
 * the server and the audio pipeline (and ffmpeg) are its children, so macOS attributes
 * their microphone use to BibleLive.
 *
 * Behaviour: `BibleLive` with no arguments (a Finder / Dock / `open` launch; a -psn_*
 * argument from older macOS is dropped) runs `python -m launcher --gui`. With arguments
 * (from Terminal: `BibleLive --download-model`, `--dry-run`, `--list-mics`...) they go to
 * the launcher. sys.executable is the bundled python3.13, so the children the launcher
 * starts are plain Python processes. Bytecode is never written inside the (signed) app;
 * the build precompiles it.
 */
#include <Python.h>
#include <limits.h>
#include <mach-o/dyld.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static int die(const char *what) {
    fprintf(stderr, "BibleLive: %s\n", what);
    return 1;
}

int main(int argc, char **argv) {
    char raw[PATH_MAX], exe[PATH_MAX];
    uint32_t size = sizeof(raw);
    if (_NSGetExecutablePath(raw, &size) != 0 || realpath(raw, exe) == NULL) {
        return die("cannot find its own path");
    }
    /* .../BibleLive.app/Contents/MacOS/BibleLive -> .../BibleLive.app/Contents/Resources */
    char res[PATH_MAX];
    char *slash = strrchr(exe, '/');
    if (slash == NULL) return die("bad executable path");
    snprintf(res, sizeof(res), "%.*s/../Resources", (int)(slash - exe), exe);
    char home[PATH_MAX], python[PATH_MAX], app[PATH_MAX];
    snprintf(home, sizeof(home), "%s/python", res);
    snprintf(python, sizeof(python), "%s/python/bin/python3.13", res);
    snprintf(app, sizeof(app), "%s/app", res);
    if (chdir(app) != 0) return die("cannot open Contents/Resources/app");

    /* Children inherit these through os.environ (the launcher copies it). */
    setenv("PYTHONDONTWRITEBYTECODE", "1", 1);
    unsetenv("PYTHONHOME");
    unsetenv("PYTHONPATH");
    unsetenv("VIRTUAL_ENV");

    /* python -m launcher [--gui | the user's arguments] */
    char **args = calloc((size_t)argc + 4, sizeof(char *));
    if (args == NULL) return die("out of memory");
    int n = 0;
    args[n++] = python;
    args[n++] = "-m";
    args[n++] = "launcher";
    int user = 0;
    for (int i = 1; i < argc; i++) {
        if (strncmp(argv[i], "-psn_", 5) == 0) continue;
        args[n++] = argv[i];
        user++;
    }
    if (user == 0) args[n++] = "--gui";

    PyStatus status;
    PyConfig config;
    PyConfig_InitPythonConfig(&config);
    config.write_bytecode = 0;
    config.use_environment = 0; /* PYTHON* variables from the user's shell are ignored */
    status = PyConfig_SetBytesString(&config, &config.home, home);
    if (PyStatus_Exception(status)) goto fail;
    status = PyConfig_SetBytesString(&config, &config.executable, python);
    if (PyStatus_Exception(status)) goto fail;
    status = PyConfig_SetBytesString(&config, &config.program_name, python);
    if (PyStatus_Exception(status)) goto fail;
    status = PyConfig_SetBytesArgv(&config, n, args);
    if (PyStatus_Exception(status)) goto fail;
    status = Py_InitializeFromConfig(&config);
    if (PyStatus_Exception(status)) goto fail;
    PyConfig_Clear(&config);
    return Py_RunMain();

fail:
    PyConfig_Clear(&config);
    Py_ExitStatusException(status);
}
