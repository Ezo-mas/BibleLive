"""English / Lithuanian UI strings for the launcher.

``t(key, **fmt)`` looks the key up in the active language and falls back to English,
then to the key itself, so a missing translation never crashes the UI.
"""

import sys

EN: dict[str, str] = {
    "app.title": "BibleLive Launcher",
    "label.speech_language": "Spoken language",
    "label.microphone": "Microphone",
    "label.projector": "Projector screen",
    "label.auto_approve": "Auto-approve",
    "label.ui_language": "Interface language",
    "label.open_display": "Open the display on the projector when starting",
    "label.open_operator": "Open the operator page when starting",
    "label.allow_network": "Allow other devices on this network",
    "ui.auto": "Automatic",
    "advanced": "Advanced",
    "adv.model": "Model",
    "adv.device": "Device",
    "adv.compute_type": "Compute type",
    "adv.port": "Port",
    "adv.log_level": "Log level",
    "adv.extra_args": "Extra audio arguments",
    "adv.speech_engine": "Lithuanian speech model",
    "btn.start": "Start",
    "btn.stop": "Stop",
    "btn.open_operator": "Open Operator",
    "btn.show_projector": "Show on Projector",
    "btn.open_logs": "Open logs folder",
    "btn.refresh": "Refresh",
    "btn.copy": "Copy",
    "mic.automatic": "(automatic)",
    "mic.loading": "Searching for microphones...",
    "projector.automatic": "Automatic (first extra screen)",
    "projector.none": "No second screen connected",
    "status.server": "Server",
    "status.audio": "Audio",
    "status.gpu": "Speech recognition",
    "state.stopped": "Stopped",
    "state.starting": "Starting...",
    "state.ready": "Ready",
    "state.running": "Running",
    "state.error": "Error",
    "gpu.checking": "Checking...",
    "gpu.cuda": "NVIDIA GPU ({device}, {compute})",
    "gpu.cpu": "Processor only - emergency mode ({compute})",
    "speech.auto": "Automatic (by graphics card)",
    "speech.whisper": "Whisper (previous setup)",
    "speech.kmynas": "Kmynas (also works without a graphics card)",
    "speech.kmynas_canary": "Kmynas + Canary (most accurate, needs a graphics card)",
    "speech.short.kmynas": "Kmynas",
    "speech.short.kmynas_canary": "Kmynas + Canary",
    "speech.where_cpu": "on the processor",
    "speech.where_gpu": "on the NVIDIA graphics card",
    "speech.status": "Lithuanian: {engine} {where}",
    "auto.off": "Off (operator approves everything)",
    "auto.high": "High confidence only",
    "auto.medium_and_up": "Medium and high confidence",
    "auto.all": "Everything",
    "log.starting_server": "Starting the server...",
    "log.starting_audio": "Starting the audio pipeline...",
    "log.server_ready": "Server is ready: {detail}",
    "log.audio_running": "Audio pipeline is running.",
    "log.stopped": "BibleLive stopped.",
    "log.stopping": "Stopping...",
    "log.projector_opened": "Display opened on the projector ({detail}).",
    "log.logs_in": "Logs are stored in {detail}",
    "net.title": "Other devices on this network",
    "net.display": "Display page",
    "net.operator": "Operator page",
    "net.token": "Operator token",
    "net.hint": (
        "Devices on the same network can open these pages; the operator page asks for "
        "the token. If a device cannot connect, allow BibleLive through Windows Firewall."
    ),
    "net.no_address": (
        "This computer's network address was not found. Connect to a network, or run "
        "ipconfig and use its IPv4 address with the same port."
    ),
    "net.log_addresses": "Other devices on this network: display {display}, operator {operator}",
    "net.log_token": "Operator token for other devices: {token}",
    "warn.no_gpu": (
        "No NVIDIA graphics card found. Running on the processor (emergency mode): "
        "transcription will be slow and less accurate."
    ),
    "warn.browser_fallback": (
        "Microsoft Edge or Chrome was not found. The display opened in the default "
        "browser on this screen: drag it to the projector and press F11."
    ),
    "err.port_in_use": (
        "Port {detail} is already in use. Close the other BibleLive window or the "
        "program using it, or change the port under Advanced."
    ),
    "err.server_failed": "The server failed to start. See the log: {detail}",
    "err.server_timeout": "The server did not become ready in time. See the log: {detail}",
    "err.server_exited": "The server stopped unexpectedly. See the log: {detail}",
    "err.audio_exited": (
        "The audio pipeline stopped. Check the microphone and see the log: {detail}"
    ),
    "err.server_spawn": "Could not start the server: {detail}",
    "err.audio_spawn": "Could not start the audio pipeline: {detail}",
    "err.projector_missing": (
        "Projector not connected. Connect the projector (extended display) and press Refresh."
    ),
    "err.projector_failed": "Could not open the display on the projector: {detail}",
    "err.invalid_port": "The port must be a number between 1 and 65535.",
    "err.invalid_args": "The extra audio arguments are not valid: {detail}",
    "err.choose_microphone": (
        "Several microphones are connected. Choose the one the sermon comes in on."
    ),
    "err.unknown": "Something went wrong: {detail}",
}

LT: dict[str, str] = {
    "app.title": "BibleLive paleidimo programa",
    "label.speech_language": "Šnekamoji kalba",
    "label.microphone": "Mikrofonas",
    "label.projector": "Projektoriaus ekranas",
    "label.auto_approve": "Automatinis patvirtinimas",
    "label.ui_language": "Sąsajos kalba",
    "label.open_display": "Paleidžiant atidaryti vaizdą projektoriuje",
    "label.open_operator": "Paleidžiant atidaryti operatoriaus puslapį",
    "label.allow_network": "Leisti kitiems įrenginiams šiame tinkle",
    "ui.auto": "Automatinė",
    "advanced": "Papildomi nustatymai",
    "adv.model": "Modelis",
    "adv.device": "Įrenginys",
    "adv.compute_type": "Skaičiavimo tipas",
    "adv.port": "Prievadas",
    "adv.log_level": "Žurnalo lygis",
    "adv.extra_args": "Papildomi garso argumentai",
    "adv.speech_engine": "Lietuvių kalbos modelis",
    "btn.start": "Paleisti",
    "btn.stop": "Sustabdyti",
    "btn.open_operator": "Atidaryti operatoriaus langą",
    "btn.show_projector": "Rodyti projektoriuje",
    "btn.open_logs": "Atidaryti žurnalų aplanką",
    "btn.refresh": "Atnaujinti",
    "btn.copy": "Kopijuoti",
    "mic.automatic": "(automatinis)",
    "mic.loading": "Ieškoma mikrofonų...",
    "projector.automatic": "Automatinis (pirmas papildomas ekranas)",
    "projector.none": "Antras ekranas neprijungtas",
    "status.server": "Serveris",
    "status.audio": "Garsas",
    "status.gpu": "Kalbos atpažinimas",
    "state.stopped": "Sustabdyta",
    "state.starting": "Paleidžiama...",
    "state.ready": "Paruošta",
    "state.running": "Veikia",
    "state.error": "Klaida",
    "gpu.checking": "Tikrinama...",
    "gpu.cuda": "NVIDIA vaizdo plokštė ({device}, {compute})",
    "gpu.cpu": "Tik procesorius - avarinis režimas ({compute})",
    "speech.auto": "Automatinis (pagal vaizdo plokštę)",
    "speech.whisper": "Whisper (ankstesnė sąranka)",
    "speech.kmynas": "Kmynas (veikia ir be vaizdo plokštės)",
    "speech.kmynas_canary": "Kmynas + Canary (tiksliausias, reikia vaizdo plokštės)",
    "speech.short.kmynas": "Kmynas",
    "speech.short.kmynas_canary": "Kmynas + Canary",
    "speech.where_cpu": "procesoriuje",
    "speech.where_gpu": "NVIDIA vaizdo plokštėje",
    "speech.status": "Lietuvių kalba: {engine} {where}",
    "auto.off": "Išjungta (viską tvirtina operatorius)",
    "auto.high": "Tik didelio tikrumo",
    "auto.medium_and_up": "Vidutinio ir didelio tikrumo",
    "auto.all": "Viskas",
    "log.starting_server": "Paleidžiamas serveris...",
    "log.starting_audio": "Paleidžiamas garso apdorojimas...",
    "log.server_ready": "Serveris paruoštas: {detail}",
    "log.audio_running": "Garso apdorojimas veikia.",
    "log.stopped": "BibleLive sustabdytas.",
    "log.stopping": "Stabdoma...",
    "log.projector_opened": "Vaizdas atidarytas projektoriuje ({detail}).",
    "log.logs_in": "Žurnalai saugomi: {detail}",
    "net.title": "Kiti įrenginiai šiame tinkle",
    "net.display": "Vaizdo puslapis",
    "net.operator": "Operatoriaus puslapis",
    "net.token": "Operatoriaus raktas",
    "net.hint": (
        "Šio tinklo įrenginiai gali atidaryti šiuos puslapius; operatoriaus puslapis "
        "paprašo rakto. Jei įrenginys nesijungia, leiskite BibleLive per Windows ugniasienę."
    ),
    "net.no_address": (
        "Nepavyko rasti šio kompiuterio tinklo adreso. Prisijunkite prie tinklo arba "
        "komandoje ipconfig raskite IPv4 adresą ir naudokite tą patį prievadą."
    ),
    "net.log_addresses": "Kiti įrenginiai tinkle: vaizdas {display}, operatorius {operator}",
    "net.log_token": "Operatoriaus raktas kitiems įrenginiams: {token}",
    "warn.no_gpu": (
        "NVIDIA vaizdo plokštė nerasta. Veikia procesorius (avarinis režimas): "
        "atpažinimas bus lėtas ir mažiau tikslus."
    ),
    "warn.browser_fallback": (
        "Nerasta Microsoft Edge ar Chrome naršyklė. Vaizdas atidarytas numatytojoje "
        "naršyklėje šiame ekrane: nutempkite jį į projektorių ir paspauskite F11."
    ),
    "err.port_in_use": (
        "Prievadas {detail} jau naudojamas. Uždarykite kitą BibleLive langą arba "
        "jį naudojančią programą, arba pakeiskite prievadą skiltyje „Papildomi nustatymai“."
    ),
    "err.server_failed": "Serveris nepasileido. Žr. žurnalą: {detail}",
    "err.server_timeout": "Serveris laiku nepasiruošė. Žr. žurnalą: {detail}",
    "err.server_exited": "Serveris netikėtai sustojo. Žr. žurnalą: {detail}",
    "err.audio_exited": (
        "Garso apdorojimas sustojo. Patikrinkite mikrofoną ir žr. žurnalą: {detail}"
    ),
    "err.server_spawn": "Nepavyko paleisti serverio: {detail}",
    "err.audio_spawn": "Nepavyko paleisti garso apdorojimo: {detail}",
    "err.projector_missing": (
        "Projektorius neprijungtas. Prijunkite projektorių (išplėstas ekranas) ir "
        "spauskite „Atnaujinti“."
    ),
    "err.projector_failed": "Nepavyko atidaryti vaizdo projektoriuje: {detail}",
    "err.invalid_port": "Prievadas turi būti skaičius nuo 1 iki 65535.",
    "err.invalid_args": "Papildomi garso argumentai neteisingi: {detail}",
    "err.choose_microphone": (
        "Prijungti keli mikrofonai. Pasirinkite tą, kuriuo ateina pamokslo garsas."
    ),
    "err.unknown": "Įvyko klaida: {detail}",
}

TABLES: dict[str, dict[str, str]] = {"en": EN, "lt": LT}
LITHUANIAN_LANG_ID = 0x27

_current = "en"


def detect_windows_ui_language() -> str:
    """Return "lt" if the Windows UI language is Lithuanian, else "en" (also off Windows)."""
    if sys.platform != "win32":
        return "en"
    try:
        import ctypes

        lang_id = int(ctypes.windll.kernel32.GetUserDefaultUILanguage())  # type: ignore[attr-defined,unused-ignore]
    except (AttributeError, OSError):
        return "en"
    return "lt" if (lang_id & 0x3FF) == LITHUANIAN_LANG_ID else "en"


def resolve_ui_language(setting: str) -> str:
    """Map the ``ui_language`` setting ("auto"|"en"|"lt") to a concrete language."""
    if setting in TABLES:
        return setting
    return detect_windows_ui_language()


def set_language(lang: str) -> None:
    global _current
    _current = lang if lang in TABLES else "en"


def get_language() -> str:
    return _current


def t(key: str, **fmt: object) -> str:
    text = TABLES[_current].get(key) or EN.get(key) or key
    if fmt:
        try:
            return text.format(**fmt)
        except (KeyError, IndexError):
            return text
    return text
