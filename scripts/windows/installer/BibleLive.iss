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
;   /SKIPGPUCHECK   do not require an NVIDIA GPU: driver 525.60+, compute capability 7.0+
;                   (GTX 1660 / RTX 2060 or newer) and 4 GB VRAM (testing only)
;   /SKIPMODEL      skip the model download and GPU self-test after copying files
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
; Room for the Whisper model that is downloaded into %LOCALAPPDATA%\BibleLive\models afterwards.
ExtraDiskSpaceRequired=1800000000
; A running BibleLive (pythonw.exe from {app}) is closed for an upgrade and not restarted.
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "lithuanian"; MessagesFile: "Lithuanian.isl"

[CustomMessages]
english.GpuMissing=No NVIDIA graphics card was detected on this computer.%n%nBibleLive needs an NVIDIA graphics card (GPU) with an up-to-date driver to transcribe speech in real time.%n%nInstall the latest driver from nvidia.com/drivers and run Setup again.
lithuanian.GpuMissing=Šiame kompiuteryje nerasta NVIDIA vaizdo plokštės.%n%n„BibleLive“ kalbą atpažįsta realiuoju laiku, todėl jai reikalinga NVIDIA vaizdo plokštė (GPU) su naujausia tvarkykle.%n%nĮdiekite naujausią tvarkyklę iš nvidia.com/drivers ir paleiskite diegimą iš naujo.
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
english.DownloadDesc=Downloading the speech model (about 1.6 GB). This happens once and may take several minutes.
lithuanian.DownloadDesc=Atsisiunčiamas kalbos atpažinimo modelis (apie 1,6 GB). Tai daroma vieną kartą ir gali užtrukti kelias minutes.
english.DownloadingModel=Downloading the speech model...
lithuanian.DownloadingModel=Atsisiunčiamas kalbos atpažinimo modelis...
english.CheckDesc=Loading the model on the graphics card. This can take up to a minute.
lithuanian.CheckDesc=Modelis įkeliamas į vaizdo plokštę. Tai gali užtrukti iki minutės.
english.CheckingGpu=Testing speech recognition on the graphics card...
lithuanian.CheckingGpu=Tikrinamas kalbos atpažinimas vaizdo plokštėje...
english.DownloadFailed=The speech model could not be downloaded.%n%n%1%n%nCheck your internet connection and the free disk space.%n%nTry again now? Choose No to continue anyway: BibleLive stays installed, and you can download the model later by running this command:%n%2
lithuanian.DownloadFailed=Nepavyko atsisiųsti kalbos atpažinimo modelio.%n%n%1%n%nPatikrinkite interneto ryšį ir laisvą vietą diske.%n%nBandyti dar kartą? Pasirinkus „Ne“, tęsite vis tiek: „BibleLive“ liks įdiegta, o modelį vėliau galėsite atsisiųsti paleidę šią komandą:%n%2
english.SelfTestFailed=The graphics card speech test failed.%n%n%1%n%nBibleLive is installed, but speech recognition may not work until this is fixed (update the NVIDIA driver and restart the computer).%n%nTry the test again now? Choose No to continue anyway; you can repeat the test later by running this command:%n%2
lithuanian.SelfTestFailed=Nepavyko vaizdo plokštės kalbos atpažinimo patikra.%n%n%1%n%n„BibleLive“ įdiegta, tačiau kalbos atpažinimas gali neveikti, kol problema nebus išspręsta (atnaujinkite NVIDIA tvarkyklę ir paleiskite kompiuterį iš naujo).%n%nBandyti patikrą dar kartą? Pasirinkus „Ne“, tęsite vis tiek; patikrą vėliau galėsite pakartoti paleidę šią komandą:%n%2
english.NoOutput=(no output)
lithuanian.NoOutput=(išvesties nėra)
english.StartNow=Start BibleLive now
lithuanian.StartNow=Paleisti „BibleLive“ dabar
english.ShortcutConsole=BibleLive (console)
lithuanian.ShortcutConsole=BibleLive (konsolė)
english.ShortcutLogs=Logs folder
lithuanian.ShortcutLogs=Žurnalų aplankas
english.AskDeleteUserData=Do you also want to delete your BibleLive settings and logs and the downloaded speech model (about 1.6 GB)?%n%n%1%n%2%n%nChoose No to keep them for a future installation.
lithuanian.AskDeleteUserData=Ar norite ištrinti ir „BibleLive“ nustatymus, žurnalus bei atsisiųstą kalbos atpažinimo modelį (apie 1,6 GB)?%n%n%1%n%2%n%nPasirinkite „Ne“, jei norite juos pasilikti būsimam diegimui.

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

function InitializeSetup(): Boolean;
var
  Gpus: array of TGpu;
  I, Best, Worst: Integer;
begin
  Result := True;
  if HasSwitch('/SKIPGPUCHECK') then
  begin
    Log('/SKIPGPUCHECK given: not checking for an NVIDIA GPU.');
    Exit;
  end;

  if not LoadGpus(Gpus) then
  begin
    Result := False;
    SuppressibleMsgBox(CustomMessage('GpuMissing'), mbCriticalError, MB_OK, IDOK);
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
    Log(Format('GPU check passed: %s, driver %s, compute capability %s, memory %d MiB.', [
      Gpus[Best].Name, Gpus[Best].Driver, FormatCc(Gpus[Best].CcTenths), Gpus[Best].MemMiB]));
    if Gpus[Best].CcTenths < 0 then
      Log('Compute capability unknown (old driver): not checked, the GPU self-test decides.');
    if (Gpus[Best].MemMiB >= 0) and (Gpus[Best].MemMiB < WarnMemMiB) then
    begin
      Log('Warning: under 6 GB of video memory, OBS streaming on this PC may stutter.');
      // Yes = continue. Silent installs only log it and continue.
      Result := SuppressibleMsgBox(FmtMessage(CustomMessage('GpuLowMemory'), [
        Gpus[Best].Name, IntToStr(Gpus[Best].MemMiB)]), mbConfirmation, MB_YESNO, IDYES) = IDYES;
    end;
    Exit;
  end;

  Result := False;
  if not DriverIsNewEnough(Gpus[Worst].Driver) then
    SuppressibleMsgBox(FmtMessage(CustomMessage('GpuDriverOld'), [Gpus[Worst].Name,
      Gpus[Worst].Driver, MinDriverText]), mbCriticalError, MB_OK, IDOK)
  else if (Gpus[Worst].CcTenths >= 0) and (Gpus[Worst].CcTenths < MinCcTenths) then
    SuppressibleMsgBox(FmtMessage(CustomMessage('GpuTooOld'), [Gpus[Worst].Name,
      FormatCc(Gpus[Worst].CcTenths), FormatCc(MinCcTenths)]), mbCriticalError, MB_OK, IDOK)
  else
    SuppressibleMsgBox(FmtMessage(CustomMessage('GpuTooLittleMemory'), [Gpus[Worst].Name,
      IntToStr(Gpus[Worst].MemMiB)]), mbCriticalError, MB_OK, IDOK);
end;

// ---------------------------------------------------------------------------------------------
// Model download and GPU self-test (after the files are copied)
// ---------------------------------------------------------------------------------------------

procedure InitializeWizard;
begin
  ModelPage := CreateOutputProgressPage(CustomMessage('ModelPageCaption'), CustomMessage('DownloadDesc'));
  CheckPage := CreateOutputMarqueeProgressPage(CustomMessage('ModelPageCaption'), CustomMessage('CheckDesc'));
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
    RunStepWithRetry('--download-model', 'DownloadFailed', True);
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
