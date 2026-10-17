; BibleLive per-user installer for Windows.
;
; Build:   powershell -ExecutionPolicy Bypass -File scripts\windows\build-installer.ps1
; Needs:   Inno Setup 6.5.0 or newer (https://jrsoftware.org/isdl.php; developed against 6.7.x).
;          6.3 added ExecAndLogOutput, 6.4 added ExecAndCaptureOutput, and the vendored
;          Lithuanian.isl is written for 6.5.0+.
; Input:   build\BibleLive\ (scripts\windows\build-portable.ps1).
; Output:  dist\BibleLive-Setup-<version>.exe
;
; Setup switches (besides the standard ones):
;   /SPEECH=cpu|gpu|whisper  the Lithuanian speech option when installing silently (default: by
;                            hardware). cpu = Kmynas on the processor, gpu = Kmynas + Canary on an
;                            NVIDIA GPU, whisper = Whisper only (the previous setup)
;   /WITHENGLISH    with the cpu option: also download the English model (Whisper small)
;   /SKIPGPUCHECK   the NVIDIA options do not require an NVIDIA GPU: driver 525.60+, compute
;                   capability 7.0+ (GTX 1660 / RTX 2060 or newer) and 4 GB VRAM (testing only)
;   /SKIPMODEL      skip the speech settings, model downloads and self-test after copying files
; Uninstall switch:
;   /DELETEUSERDATA with a silent uninstall, also delete settings, logs and the model

#if VER < EncodeVer(6, 5, 0)
  #error BibleLive.iss needs Inno Setup 6.5.0 or newer.
#endif

#ifndef AppVersion
  #error Pass the version with /DAppVersion=x.y.z (scripts\windows\build-installer.ps1 does).
#endif
#ifndef VersionNumeric
  #define VersionNumeric AppVersion
#endif
#ifndef BuildDir
  #define BuildDir "..\..\..\build\BibleLive"
#endif
#ifndef OutputDir
  #define OutputDir "..\..\..\dist"
#endif

#define AppName "BibleLive"

[Setup]
; Never change the AppId: it is how a newer Setup finds and upgrades an existing install.
AppId={{CCC6CB55-0186-4AD8-B558-54A8A4956583}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=BibleLive Contributors
VersionInfoVersion={#VersionNumeric}
VersionInfoProductName={#AppName}
VersionInfoDescription={#AppName} Setup
DefaultDirName={localappdata}\Programs\BibleLive
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#OutputDir}
OutputBaseFilename=BibleLive-Setup-{#AppVersion}
SetupIconFile=biblelive.ico
UninstallDisplayIcon={app}\biblelive.ico
UninstallDisplayName={#AppName}
LicenseFile=..\..\..\LICENSE
WizardStyle=modern
SetupLogging=yes
; The ~2.5 GB payload is mostly NVIDIA DLLs and compresses to roughly 1.4 GB. lzma2/ultra64 needs
; ~750 MB of RAM per compression thread (6 MB + 11.5 x 64 MB dictionary); two block threads plus the
; separate compressor process keep that within a normal build machine, including the 32-bit ISCC.
; Decompression on the user's PC needs only the 64 MB dictionary. If a build host runs out of
; memory, switch to lzma2/max (8 MB dictionary, ~100 MB) at a small size cost.
Compression=lzma2/ultra64
SolidCompression=yes
LZMANumBlockThreads=2
LZMAUseSeparateProcess=yes
; Inno stores up to 4.2 GB in a single Setup.exe, so no DiskSpanning is needed for ~1.4 GB.
DiskSpanning=no
; Room for the speech models downloaded into %LOCALAPPDATA%\BibleLive\models afterwards (up to about 3.5 GB).
ExtraDiskSpaceRequired=4000000000
; A running BibleLive (pythonw.exe from {app}) is closed for an upgrade and not restarted.
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "lithuanian"; MessagesFile: "Lithuanian.isl"

[CustomMessages]
english.GpuMissing=No NVIDIA graphics card was detected on this computer.%n%nThe NVIDIA options need an NVIDIA graphics card (GPU) with an up-to-date driver. Go back and choose the processor option, or install the latest driver from nvidia.com/drivers and run Setup again.
lithuanian.GpuMissing=Šiame kompiuteryje nerasta NVIDIA vaizdo plokštės.%n%nNVIDIA parinktims reikalinga NVIDIA vaizdo plokštė (GPU) su naujausia tvarkykle. Grįžkite ir pasirinkite procesoriaus parinktį arba įdiegę naujausią tvarkyklę iš nvidia.com/drivers paleiskite diegimą iš naujo.
english.GpuDriverOld=The NVIDIA driver on this computer is too old.%n%nGraphics card: %1%nDriver version: %2%nRequired: %3 or newer%n%nUpdate the driver from nvidia.com/drivers and run Setup again.
lithuanian.GpuDriverOld=Šiame kompiuteryje įdiegta per sena NVIDIA tvarkyklė.%n%nVaizdo plokštė: %1%nTvarkyklės versija: %2%nReikalinga: %3 arba naujesnė%n%nAtnaujinkite tvarkyklę iš nvidia.com/drivers ir paleiskite diegimą iš naujo.
english.GpuTooOld=The graphics card in this computer is too old.%n%nGraphics card: %1%nCompute capability: %2 (%3 or newer is required)%n%nBibleLive needs an NVIDIA graphics card of the GTX 1660 / RTX 2060 generation or newer. The GTX 10 series and older cards cannot run the speech model.
lithuanian.GpuTooOld=Šiame kompiuteryje įdiegta per sena vaizdo plokštė.%n%nVaizdo plokštė: %1%nSkaičiavimo galimybių versija (compute capability): %2 (reikalinga %3 arba naujesnė)%n%n„BibleLive“ reikalinga NVIDIA vaizdo plokštė, ne senesnė už GTX 1660 / RTX 2060. GTX 10 serijos ir senesnės plokštės kalbos atpažinimo modelio paleisti negali.
english.GpuTooLittleMemory=The graphics card in this computer has too little video memory (VRAM).%n%nGraphics card: %1%nVideo memory: %2 MB%n%nBibleLive needs at least 4 GB of video memory; 6 GB or more is recommended.
lithuanian.GpuTooLittleMemory=Šio kompiuterio vaizdo plokštėje per mažai vaizdo atminties (VRAM).%n%nVaizdo plokštė: %1%nVaizdo atmintis: %2 MB%n%n„BibleLive“ reikia bent 4 GB vaizdo atminties; rekomenduojama 6 GB ar daugiau.
english.GpuLowMemory=Graphics card: %1%nVideo memory: %2 MB%n%nBibleLive will work, but if you also livestream with OBS on this computer, the picture may stutter. 6 GB or more of video memory is recommended.%n%nContinue with the installation?
lithuanian.GpuLowMemory=Vaizdo plokštė: %1%nVaizdo atmintis: %2 MB%n%n„BibleLive“ veiks, tačiau jei šiame kompiuteryje taip pat transliuojate su OBS, vaizdas gali strigti. Rekomenduojama 6 GB ar daugiau vaizdo atminties.%n%nTęsti diegimą?
english.ModelPageCaption=Setting up speech recognition
lithuanian.ModelPageCaption=Kalbos atpažinimo paruošimas
english.DownloadDesc=Downloading the speech models (up to about 3.5 GB). This happens once and may take several minutes.
lithuanian.DownloadDesc=Atsisiunčiami kalbos atpažinimo modeliai (iki maždaug 3,5 GB). Tai daroma vieną kartą ir gali užtrukti kelias minutes.
english.DownloadingModel=Downloading the speech model...
lithuanian.DownloadingModel=Atsisiunčiamas kalbos atpažinimo modelis...
english.CheckDesc=Loading the speech models. This can take up to a minute.
lithuanian.CheckDesc=Kalbos atpažinimo modeliai įkeliami. Tai gali užtrukti iki minutės.
english.CheckingGpu=Testing speech recognition...
lithuanian.CheckingGpu=Tikrinamas kalbos atpažinimas...
english.DownloadFailed=The speech models could not be downloaded.%n%n%1%n%nCheck your internet connection and the free disk space.%n%nTry again now? Choose No to continue anyway: BibleLive stays installed, and you can download the models later by running this command:%n%2
lithuanian.DownloadFailed=Nepavyko atsisiųsti kalbos atpažinimo modelių.%n%n%1%n%nPatikrinkite interneto ryšį ir laisvą vietą diske.%n%nBandyti dar kartą? Pasirinkus „Ne“, tęsite vis tiek: „BibleLive“ liks įdiegta, o modelius vėliau galėsite atsisiųsti paleidę šią komandą:%n%2
english.SelfTestFailed=The speech recognition test failed.%n%n%1%n%nBibleLive is installed, but speech recognition may not work until this is fixed (for an NVIDIA graphics card, update its driver and restart the computer).%n%nTry the test again now? Choose No to continue anyway; you can repeat the test later by running this command:%n%2
lithuanian.SelfTestFailed=Nepavyko kalbos atpažinimo patikra.%n%n%1%n%n„BibleLive“ įdiegta, tačiau kalbos atpažinimas gali neveikti, kol problema nebus išspręsta (NVIDIA vaizdo plokštei atnaujinkite tvarkyklę ir paleiskite kompiuterį iš naujo).%n%nBandyti patikrą dar kartą? Pasirinkus „Ne“, tęsite vis tiek; patikrą vėliau galėsite pakartoti paleidę šią komandą:%n%2
english.NoOutput=(no output)
lithuanian.NoOutput=(išvesties nėra)
english.StartNow=Start BibleLive now
lithuanian.StartNow=Paleisti „BibleLive“ dabar
english.ShortcutConsole=BibleLive (console)
lithuanian.ShortcutConsole=BibleLive (konsolė)
english.ShortcutLogs=Logs folder
lithuanian.ShortcutLogs=Žurnalų aplankas
english.AskDeleteUserData=Do you also want to delete your BibleLive settings and logs and the downloaded speech models (several GB)?%n%n%1%n%2%n%nChoose No to keep them for a future installation.
lithuanian.AskDeleteUserData=Ar norite ištrinti ir „BibleLive“ nustatymus, žurnalus bei atsisiųstus kalbos atpažinimo modelius (kelis GB)?%n%n%1%n%2%n%nPasirinkite „Ne“, jei norite juos pasilikti būsimam diegimui.
english.SpeechPageCaption=Speech recognition
english.SpeechPageDesc=Choose how BibleLive recognises Lithuanian speech. You can change this later under Advanced in the launcher.
english.SpeechCpuOption=Lithuanian on CPU — Kmynas (no graphics card needed)
english.SpeechGpuOption=Lithuanian on NVIDIA GPU — Kmynas + Canary (most accurate)
english.SpeechWhisperOption=Whisper only (previous setup)
english.WhisperPageCaption=English speech recognition
english.WhisperPageDesc=English services use the Whisper model, which is slow on a processor. This choice does not affect Lithuanian.
english.WhisperNoOption=No, Lithuanian only (recommended)
english.WhisperYesOption=Yes, also download the English model (Whisper small, about 0.5 GB)
lithuanian.SpeechPageCaption=Kalbos atpažinimas
lithuanian.SpeechPageDesc=Pasirinkite, kaip „BibleLive“ atpažins lietuvių kalbą. Vėliau tai galima pakeisti programos išplėstiniuose nustatymuose.
lithuanian.SpeechCpuOption=Lietuvių kalba procesoriuje — Kmynas (vaizdo plokštė nereikalinga)
lithuanian.SpeechGpuOption=Lietuvių kalba NVIDIA vaizdo plokštėje — Kmynas + Canary (tiksliausia)
lithuanian.SpeechWhisperOption=Tik Whisper (ankstesnė sąranka)
lithuanian.WhisperPageCaption=Anglų kalbos atpažinimas
lithuanian.WhisperPageDesc=Angliškiems pamokslams naudojamas Whisper modelis, kuris procesoriuje veikia lėtai. Lietuvių kalbai šis pasirinkimas įtakos neturi.
lithuanian.WhisperNoOption=Ne, tik lietuvių kalba (rekomenduojama)
lithuanian.WhisperYesOption=Taip, atsisiųsti ir angliško atpažinimo modelį (Whisper small, apie 0,5 GB)

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#BuildDir}\python\*"; DestDir: "{app}\python"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#BuildDir}\app\*"; DestDir: "{app}\app"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#BuildDir}\licenses\*"; DestDir: "{app}\licenses"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#BuildDir}\BibleLive.cmd"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#BuildDir}\BibleLive-console.cmd"; DestDir: "{app}"; Flags: ignoreversion
Source: "biblelive.ico"; DestDir: "{app}"; Flags: ignoreversion

[Dirs]
Name: "{userappdata}\BibleLive\logs"

[InstallDelete]
; An upgrade installs over the old version: drop everything Setup owns first so modules and packages
; that no longer exist do not linger. User data lives outside {app} (%APPDATA% / %LOCALAPPDATA%).
Type: filesandordirs; Name: "{app}\python"
Type: filesandordirs; Name: "{app}\app\backend"
Type: filesandordirs; Name: "{app}\app\static"
Type: filesandordirs; Name: "{app}\app\ffmpeg"
Type: filesandordirs; Name: "{app}\licenses"

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\python\pythonw.exe"; Parameters: "-m launcher"; WorkingDir: "{app}\app"; IconFilename: "{app}\biblelive.ico"
Name: "{group}\{cm:ShortcutConsole}"; Filename: "{app}\BibleLive-console.cmd"; WorkingDir: "{app}\app"; IconFilename: "{app}\biblelive.ico"
Name: "{group}\{cm:ShortcutLogs}"; Filename: "{userappdata}\BibleLive\logs"; IconFilename: "{app}\biblelive.ico"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"; IconFilename: "{app}\biblelive.ico"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\python\pythonw.exe"; Parameters: "-m launcher"; WorkingDir: "{app}\app"; IconFilename: "{app}\biblelive.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\python\pythonw.exe"; Parameters: "-m launcher"; WorkingDir: "{app}\app"; Description: "{cm:StartNow}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Python writes __pycache__ folders next to the installed files; Setup does not know about them.
Type: filesandordirs; Name: "{app}\python"
Type: filesandordirs; Name: "{app}\app"

[Code]
const
  MinDriverMajor = 525;
  MinDriverMinor = 60;
  MinDriverText = '525.60';
  // float16 needs compute capability 7.0 (GTX 16xx / RTX 20xx+): stored in tenths.
  MinCcTenths = 70;
  // 4 GiB is the floor; under 6 GiB OBS (NVENC) on the same PC may stutter.
  MinMemMiB = 4096;
  WarnMemMiB = 6144;
  Quote = '''';

type
  TGpu = record
    Name: String;
    Driver: String;
    CcTenths: Integer;  // -1 = unknown
    MemMiB: Integer;    // -1 = unknown
  end;

function SetEnvironmentVariableW(lpName, lpValue: String): Boolean;
  external 'SetEnvironmentVariableW@kernel32.dll stdcall';

var
  ModelPage: TOutputProgressWizardPage;
  CheckPage: TOutputMarqueeProgressWizardPage;
  ShowingModelPage: Boolean;
  LastOutput: String;
  SpeechPage: TInputOptionWizardPage;
  WhisperPage: TInputOptionWizardPage;
  GpuOk: Boolean;  // a usable NVIDIA GPU was found (see CheckNvidia)
  GpuProblem: String;  // why not: shown when an NVIDIA option is chosen
  GpuLowMemory: String;  // question for a card under 6 GB (empty = none)

function HasSwitch(const Name: String): Boolean;
var
  I: Integer;
begin
  Result := False;
  for I := 1 to ParamCount do
    if CompareText(ParamStr(I), Name) = 0 then
    begin
      Result := True;
      Exit;
    end;
end;

// ---------------------------------------------------------------------------------------------
// NVIDIA pre-install check
// ---------------------------------------------------------------------------------------------

// True if "major.minor[.x]" is at least MinDriverMajor.MinDriverMinor (e.g. 551.23, 525.60.13).
function DriverIsNewEnough(const Version: String): Boolean;
var
  P, Major, Minor: Integer;
  Rest, MinorText: String;
begin
  Result := False;
  P := Pos('.', Version);
  if P = 0 then
    Exit;
  Major := StrToIntDef(Copy(Version, 1, P - 1), -1);
  Rest := Copy(Version, P + 1, Length(Version));
  P := Pos('.', Rest);
  if P > 0 then
    MinorText := Copy(Rest, 1, P - 1)
  else
    MinorText := Rest;
  Minor := StrToIntDef(MinorText, -1);
  if (Major < 0) or (Minor < 0) then
    Exit;
  Result := (Major > MinDriverMajor) or ((Major = MinDriverMajor) and (Minor >= MinDriverMinor));
end;

// "7.5" -> 75, "12.0" -> 120; -1 if it is not a number ("[N/A]", empty).
function ParseCcTenths(const S: String): Integer;
var
  P, Major, Minor: Integer;
  T: String;
begin
  Result := -1;
  T := Trim(S);
  P := Pos('.', T);
  if P = 0 then
    Exit;
  Major := StrToIntDef(Copy(T, 1, P - 1), -1);
  Minor := StrToIntDef(Copy(T, P + 1, 1), -1);
  if (Major < 0) or (Minor < 0) then
    Exit;
  Result := Major * 10 + Minor;
end;

function FormatCc(Tenths: Integer): String;
begin
  if Tenths < 0 then
    Result := '?'
  else
    Result := IntToStr(Tenths div 10) + '.' + IntToStr(Tenths mod 10);
end;

// Parses "NVIDIA GeForce RTX 3060, 551.23, 8.6, 12288" (name, driver, compute capability, MiB) or
// the two-column "name, 551.23" that is all an old driver can report (capability / VRAM -> -1).
function ParseGpuRow(const Line: String; var Gpu: TGpu): Boolean;
var
  Cells: array of String;
  Count, I, Start, NameCells: Integer;
begin
  Result := False;
  Count := 0;
  Start := 1;
  for I := 1 to Length(Line) + 1 do
    if (I > Length(Line)) or (Line[I] = ',') then
    begin
      SetArrayLength(Cells, Count + 1);
      Cells[Count] := Trim(Copy(Line, Start, I - Start));
      Count := Count + 1;
      Start := I + 1;
    end;
  if Count < 2 then
    Exit;
  Gpu.CcTenths := -1;
  Gpu.MemMiB := -1;
  if Count >= 4 then
  begin
    NameCells := Count - 3;
    Gpu.Driver := Cells[Count - 3];
    Gpu.CcTenths := ParseCcTenths(Cells[Count - 2]);
    Gpu.MemMiB := StrToIntDef(Cells[Count - 1], -1);
  end
  else
  begin
    NameCells := Count - 1;
    Gpu.Driver := Cells[Count - 1];
  end;
  Gpu.Name := Cells[0];
  for I := 1 to NameCells - 1 do
    Gpu.Name := Gpu.Name + ', ' + Cells[I];
  Result := (Gpu.Name <> '') and (Gpu.Driver <> '');
end;

// Runs nvidia-smi with the given query fields; False if it is missing or fails (exit code <> 0,
// which is what a driver that does not know a field such as compute_cap does).
function QueryNvidiaSmi(const Exe, Fields: String; var Lines: TArrayOfString): Boolean;
var
  Output: TExecOutput;
  ResultCode: Integer;
begin
  Result := False;
  try
    if ExecAndCaptureOutput(Exe, '--query-gpu=' + Fields + ' --format=csv,noheader,nounits', '',
      SW_HIDE, ewWaitUntilTerminated, ResultCode, Output) then
    begin
      Log(Format('%s (%s) exited with %d', [Exe, Fields, ResultCode]));
      if (ResultCode = 0) and (GetArrayLength(Output.StdOut) > 0) then
      begin
        Lines := Output.StdOut;
        Result := True;
      end;
    end;
  except
    Log('nvidia-smi failed: ' + GetExceptionMessage);
  end;
end;

function FindNvidiaSmi(const Fields: String; var Lines: TArrayOfString): Boolean;
begin
  // The driver installs nvidia-smi.exe into System32; older drivers use the NVSMI folder.
  Result := QueryNvidiaSmi(ExpandConstant('{sys}\nvidia-smi.exe'), Fields, Lines);
  if not Result then
    Result := QueryNvidiaSmi(ExpandConstant('{commonpf64}\NVIDIA Corporation\NVSMI\nvidia-smi.exe'), Fields, Lines);
  if not Result then
    Result := QueryNvidiaSmi('nvidia-smi.exe', Fields, Lines);
end;

// Reads every GPU. If the driver cannot report compute_cap / memory.total, falls back to
// name + driver only (the hardware limits are then not checked; the GPU self-test still runs).
function LoadGpus(var Gpus: array of TGpu): Boolean;
var
  Lines: TArrayOfString;
  I, N: Integer;
  Gpu: TGpu;
begin
  Result := False;
  SetArrayLength(Gpus, 0);
  if not FindNvidiaSmi('name,driver_version,compute_cap,memory.total', Lines) then
  begin
    Log('The compute_cap / memory.total query failed (old driver?): checking name and driver only.');
    if not FindNvidiaSmi('name,driver_version', Lines) then
      Exit;
  end;
  N := 0;
  for I := 0 to GetArrayLength(Lines) - 1 do
  begin
    Log('GPU: ' + Lines[I]);
    if ParseGpuRow(Lines[I], Gpu) then
    begin
      SetArrayLength(Gpus, N + 1);
      Gpus[N] := Gpu;
      N := N + 1;
    end;
  end;
  Result := N > 0;
end;

function GpuMeetsMinimum(const Gpu: TGpu): Boolean;
begin
  // Unknown (-1) values never block.
  Result := DriverIsNewEnough(Gpu.Driver)
    and ((Gpu.CcTenths < 0) or (Gpu.CcTenths >= MinCcTenths))
    and ((Gpu.MemMiB < 0) or (Gpu.MemMiB >= MinMemMiB));
end;

// Looks for an NVIDIA GPU that meets the minimum, once at start-up. GpuOk says whether one does;
// otherwise GpuProblem says why (shown only when an NVIDIA option is chosen). GpuLowMemory holds
// the question for a card under 6 GB.
procedure CheckNvidia;
var
  Gpus: array of TGpu;
  I, Best, Worst: Integer;
begin
  GpuOk := False;
  GpuProblem := '';
  GpuLowMemory := '';
  if not LoadGpus(Gpus) then
  begin
    GpuProblem := CustomMessage('GpuMissing');
    Exit;
  end;

  // Best = the qualifying GPU with the most VRAM. Worst = (when none qualifies) the strongest
  // GPU, so the message names the card closest to working.
  Best := -1;
  Worst := 0;
  for I := 0 to GetArrayLength(Gpus) - 1 do
  begin
    Log(Format('GPU %d: %s, driver %s, compute capability %s, memory %d MiB, qualifies: %d', [
      I, Gpus[I].Name, Gpus[I].Driver, FormatCc(Gpus[I].CcTenths), Gpus[I].MemMiB,
       Ord(GpuMeetsMinimum(Gpus[I]))]));
    if GpuMeetsMinimum(Gpus[I]) and ((Best < 0) or (Gpus[I].MemMiB > Gpus[Best].MemMiB)) then
      Best := I;
    if (Gpus[I].CcTenths > Gpus[Worst].CcTenths)
      or ((Gpus[I].CcTenths = Gpus[Worst].CcTenths) and (Gpus[I].MemMiB > Gpus[Worst].MemMiB)) then
      Worst := I;
  end;

  if Best >= 0 then
  begin
    GpuOk := True;
    Log(Format('GPU check passed: %s, driver %s, compute capability %s, memory %d MiB.', [
      Gpus[Best].Name, Gpus[Best].Driver, FormatCc(Gpus[Best].CcTenths), Gpus[Best].MemMiB]));
    if Gpus[Best].CcTenths < 0 then
      Log('Compute capability unknown (old driver): not checked, the speech test decides.');
    if (Gpus[Best].MemMiB >= 0) and (Gpus[Best].MemMiB < WarnMemMiB) then
    begin
      Log('Warning: under 6 GB of video memory, OBS streaming on this PC may stutter.');
      GpuLowMemory := FmtMessage(CustomMessage('GpuLowMemory'), [
        Gpus[Best].Name, IntToStr(Gpus[Best].MemMiB)]);
    end;
    Exit;
  end;

  if not DriverIsNewEnough(Gpus[Worst].Driver) then
    GpuProblem := FmtMessage(CustomMessage('GpuDriverOld'), [Gpus[Worst].Name,
      Gpus[Worst].Driver, MinDriverText])
  else if (Gpus[Worst].CcTenths >= 0) and (Gpus[Worst].CcTenths < MinCcTenths) then
    GpuProblem := FmtMessage(CustomMessage('GpuTooOld'), [Gpus[Worst].Name,
      FormatCc(Gpus[Worst].CcTenths), FormatCc(MinCcTenths)])
  else
    GpuProblem := FmtMessage(CustomMessage('GpuTooLittleMemory'), [Gpus[Worst].Name,
      IntToStr(Gpus[Worst].MemMiB)]);
end;

// True if an NVIDIA option may go on: a qualifying card, or /SKIPGPUCHECK (testing only). A card
// under 6 GB asks first; silent installs log it and continue.
function NvidiaAllowed(): Boolean;
begin
  if HasSwitch('/SKIPGPUCHECK') then
  begin
    Log('/SKIPGPUCHECK given: not checking for an NVIDIA GPU.');
    Result := True;
  end
  else if not GpuOk then
  begin
    SuppressibleMsgBox(GpuProblem, mbCriticalError, MB_OK, IDOK);
    Result := False;
  end
  else if GpuLowMemory <> '' then
    // Yes = continue. Silent installs only log it and continue.
    Result := SuppressibleMsgBox(GpuLowMemory, mbConfirmation, MB_YESNO, IDYES) = IDYES
  else
    Result := True;
end;

// /SPEECH=cpu|gpu|whisper picks the speech option; '' when the switch is not given.
function SpeechSwitch(): String;
var
  I: Integer;
  Arg: String;
begin
  Result := '';
  for I := 1 to ParamCount do
  begin
    Arg := UpperCase(ParamStr(I));
    if Pos('/SPEECH=', Arg) = 1 then
      Result := LowerCase(Copy(Arg, 9, Length(Arg)));
  end;
end;

function InitializeSetup(): Boolean;
begin
  CheckNvidia;
  Result := True;
  // A silent install that asks for an NVIDIA option without the hardware is refused here. An
  // interactive install gets the same message when it leaves the speech page.
  if WizardSilent and ((SpeechSwitch() = 'gpu') or (SpeechSwitch() = 'whisper')) then
    Result := NvidiaAllowed();
end;

// ---------------------------------------------------------------------------------------------
// Model download and GPU self-test (after the files are copied)
// ---------------------------------------------------------------------------------------------

procedure InitializeWizard;
var
  Choice: String;
begin
  SpeechPage := CreateInputOptionPage(wpSelectTasks, CustomMessage('SpeechPageCaption'),
    CustomMessage('SpeechPageDesc'), '', True, False);
  SpeechPage.Add(CustomMessage('SpeechCpuOption'));
  SpeechPage.Add(CustomMessage('SpeechGpuOption'));
  SpeechPage.Add(CustomMessage('SpeechWhisperOption'));
  Choice := SpeechSwitch();
  if Choice = 'cpu' then
    SpeechPage.SelectedValueIndex := 0
  else if Choice = 'gpu' then
    SpeechPage.SelectedValueIndex := 1
  else if Choice = 'whisper' then
    SpeechPage.SelectedValueIndex := 2
  else if GpuOk then
    // By hardware: the NVIDIA option when a qualifying card is there, else the processor option.
    SpeechPage.SelectedValueIndex := 1
  else
    SpeechPage.SelectedValueIndex := 0;

  WhisperPage := CreateInputOptionPage(SpeechPage.ID, CustomMessage('WhisperPageCaption'),
    CustomMessage('WhisperPageDesc'), '', True, False);
  WhisperPage.Add(CustomMessage('WhisperNoOption'));
  WhisperPage.Add(CustomMessage('WhisperYesOption'));
  if HasSwitch('/WITHENGLISH') then
    WhisperPage.SelectedValueIndex := 1
  else
    WhisperPage.SelectedValueIndex := 0;

  ModelPage := CreateOutputProgressPage(CustomMessage('ModelPageCaption'), CustomMessage('DownloadDesc'));
  CheckPage := CreateOutputMarqueeProgressPage(CustomMessage('ModelPageCaption'), CustomMessage('CheckDesc'));
end;

// The English-model question is asked only for the processor option.
function ShouldSkipPage(CurPageID: Integer): Boolean;
begin
  Result := (CurPageID = WhisperPage.ID) and (SpeechPage.SelectedValueIndex <> 0);
end;

// The NVIDIA options (1 and 2) need a graphics card; the processor option does not.
function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if (CurPageID = SpeechPage.ID) and (SpeechPage.SelectedValueIndex > 0) then
    Result := NvidiaAllowed();
end;

// The launcher arguments for the chosen speech option: its settings, and the models to download.
function SpeechArgs(): String;
begin
  if SpeechPage.SelectedValueIndex = 0 then
  begin
    // The Lithuanian options start the pipeline in Lithuanian, so a CPU-only PC never loads
    // Whisper at start (the operator can still switch to English at runtime).
    Result := '--speech-engine kmynas --device cpu --model small --language lt';
    if WhisperPage.SelectedValueIndex = 0 then
      Result := Result + ' --no-whisper';
  end
  else if SpeechPage.SelectedValueIndex = 1 then
    Result := '--speech-engine kmynas+canary --device auto --model large-v3-turbo --language lt'
  else
    Result := '--speech-engine whisper --device auto --model large-v3-turbo';
  Result := '--download-model ' + Result + ' --save';
end;

// Called once per output line of the launcher command. The download prints
// "  progress: 812 MB / ~1620 MB (50%)"; every other line is just shown as status text.
procedure OnLauncherLog(const S: String; const Error, FirstLine: Boolean);
var
  P, Q, Percent: Integer;
  Line: String;
begin
  Line := Trim(S);
  if Line = '' then
    Exit;
  Log('launcher: ' + Line);
  if Error then
    Exit;
  LastOutput := Line;
  if ShowingModelPage then
  begin
    P := Pos('MB (', Line);
    Q := Pos('%)', Line);
    if (P > 0) and (Q > P + 4) then
    begin
      Percent := StrToIntDef(Copy(Line, P + 4, Q - P - 4), -1);
      if (Percent >= 0) and (Percent <= 100) then
        ModelPage.SetProgress(Percent, 100);
    end;
    ModelPage.SetText(CustomMessage('DownloadingModel'), Line);
  end
  else
  begin
    CheckPage.SetText(CustomMessage('CheckingGpu'), Line);
    CheckPage.Animate;
  end;
end;

// Runs "python -m launcher <Flag>" from {app}\app with the page shown; True on exit code 0.
// The launcher sets HF_HOME (models folder) itself, exactly as it does when it starts the server.
function RunLauncherStep(const Flag: String; ShowDownloadPage: Boolean): Boolean;
var
  ResultCode: Integer;
begin
  Result := False;
  LastOutput := '';
  ShowingModelPage := ShowDownloadPage;
  if ShowDownloadPage then
  begin
    ModelPage.SetText(CustomMessage('DownloadingModel'), '');
    ModelPage.SetProgress(0, 100);
    ModelPage.Show;
  end
  else
  begin
    CheckPage.SetText(CustomMessage('CheckingGpu'), '');
    CheckPage.Show;
    CheckPage.Animate;
  end;
  try
    try
      Result := ExecAndLogOutput(ExpandConstant('{app}\python\python.exe'), '-m launcher ' + Flag,
        ExpandConstant('{app}\app'), SW_SHOWNORMAL, ewWaitUntilTerminated, ResultCode,
        @OnLauncherLog) and (ResultCode = 0);
      Log(Format('launcher %s exited with %d', [Flag, ResultCode]));
    except
      LastOutput := GetExceptionMessage;
      Log('launcher ' + Flag + ' failed to run: ' + LastOutput);
    end;
  finally
    if ShowDownloadPage then
      ModelPage.Hide
    else
      CheckPage.Hide;
  end;
end;

procedure RunStepWithRetry(const Flag, FailedMessage: String; ShowDownloadPage: Boolean);
var
  Again: Boolean;
  Detail: String;
begin
  repeat
    Again := False;
    if not RunLauncherStep(Flag, ShowDownloadPage) then
    begin
      Detail := LastOutput;
      if Detail = '' then
        Detail := CustomMessage('NoOutput');
      if Length(Detail) > 400 then
        Detail := Copy(Detail, 1, 400) + '...';
      // Yes = try again, No = continue anyway. Silent installs continue.
      Again := SuppressibleMsgBox(FmtMessage(CustomMessage(FailedMessage), [Detail,
         '"' + ExpandConstant('{app}\BibleLive-console.cmd') + '" ' + Flag]),
        mbError, MB_YESNO, IDNO) = IDYES;
    end;
  until not Again;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if (CurStep = ssPostInstall) and not HasSwitch('/SKIPMODEL') then
  begin
    // Do not let a PYTHONHOME / PYTHONPATH from the user's environment confuse the bundled Python.
    SetEnvironmentVariableW('PYTHONHOME', '');
    SetEnvironmentVariableW('PYTHONPATH', '');
    SetEnvironmentVariableW('VIRTUAL_ENV', '');
    RunStepWithRetry(SpeechArgs(), 'DownloadFailed', True);
    RunStepWithRetry('--gpu-self-test', 'SelfTestFailed', False);
  end;
end;

// ---------------------------------------------------------------------------------------------
// Uninstall
// ---------------------------------------------------------------------------------------------

function PsStartsWith(const Dir: String): String;
var
  Escaped: String;
begin
  Escaped := Dir;
  StringChangeEx(Escaped, Quote, Quote + Quote, True);
  Result := '$_.ExecutablePath.StartsWith(' + Quote + Escaped + Quote + ', [StringComparison]::OrdinalIgnoreCase)';
end;

// Ends BibleLive processes (launcher, server, audio pipeline, ffmpeg) that run from {app}\python
// or {app}\app so their files can be deleted. Processes outside {app} (such as the browser showing
// the display page) are left alone.
procedure StopAppProcesses;
var
  ResultCode: Integer;
  Command: String;
begin
  Command := '-NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "Get-CimInstance Win32_Process'
    + ' | Where-Object { $_.ExecutablePath -and (' + PsStartsWith(ExpandConstant('{app}\python\'))
    + ' -or ' + PsStartsWith(ExpandConstant('{app}\app\')) + ') }'
    + ' | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"';
  if not Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'), Command, '', SW_HIDE,
    ewWaitUntilTerminated, ResultCode) then
    Log('Could not run PowerShell to stop BibleLive processes.');
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  SettingsDir, ModelsDir: String;
  DeleteData: Boolean;
begin
  if CurUninstallStep = usUninstall then
    StopAppProcesses;

  if CurUninstallStep = usPostUninstall then
  begin
    SettingsDir := ExpandConstant('{userappdata}\BibleLive');
    ModelsDir := ExpandConstant('{localappdata}\BibleLive');
    if UninstallSilent then
      DeleteData := HasSwitch('/DELETEUSERDATA')
    else
      DeleteData := MsgBox(FmtMessage(CustomMessage('AskDeleteUserData'), [SettingsDir, ModelsDir]),
        mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES;
    if DeleteData then
    begin
      DelTree(SettingsDir, True, True, True);
      DelTree(ModelsDir, True, True, True);
    end;
  end;
end;
