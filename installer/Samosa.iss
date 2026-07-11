#ifndef MyAppVersion
  #define MyAppVersion "1.1.0"
#endif

#define MyAppName "Samosa"
#define MyAppPublisher "Tenet Motion"
#define MyAppURL "https://github.com/tenetmotion/Samosa"

[Setup]
AppId={{ED3E0BCD-8E49-4E16-B3EB-102F1034A9B8}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}/releases
DefaultDirName={localappdata}\Programs\Samosa
DefaultGroupName=Samosa
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist\installer
OutputBaseFilename=Samosa-Setup-{#MyAppVersion}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
LicenseFile=..\LICENSE
UninstallDisplayName=Samosa
VersionInfoVersion={#MyAppVersion}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription=Samosa for Adobe After Effects online installer
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppVersion}

[Files]
Source: "..\panel\*"; DestDir: "{app}\cep"; Excludes: "config.json,__pycache__\*,*.pyc"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\backend\*"; DestDir: "{app}\backend"; Excludes: "__pycache__\*,*.pyc"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "bootstrap.ps1"; DestDir: "{app}\installer"; Flags: ignoreversion
Source: "download_models.py"; DestDir: "{app}\installer"; Flags: ignoreversion
Source: "manage-models.ps1"; DestDir: "{app}\installer"; Flags: ignoreversion
Source: "..\docs\*"; DestDir: "{app}\docs"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\NOTICE.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\THIRD_PARTY_NOTICES.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\CHANGELOG.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\Installation guide"; Filename: "{app}\docs\INSTALL.md"
Name: "{group}\After Effects tutorial"; Filename: "{app}\docs\AFTER_EFFECTS_TUTORIAL.md"
Name: "{group}\Manage model packs"; Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoExit -NoProfile -ExecutionPolicy Bypass -File ""{app}\installer\manage-models.ps1"" -InstallRoot ""{app}"""

[UninstallRun]
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\installer\bootstrap.ps1"" -InstallRoot ""{app}"" -Uninstall"; Flags: runhidden waituntilterminated; RunOnceId: "RemoveCepRegistration"

[UninstallDelete]
Type: filesandordirs; Name: "{app}\runtime"
Type: filesandordirs; Name: "{app}\downloads"
Type: filesandordirs; Name: "{app}\logs"
Type: filesandordirs; Name: "{app}\cep"
Type: files; Name: "{app}\install-state.json"

[Code]
var
  BackendPage: TInputOptionWizardPage;
  ModePage: TInputOptionWizardPage;
  CustomPage: TInputOptionWizardPage;
  LicensePage: TInputOptionWizardPage;

procedure InitializeWizard;
begin
  BackendPage := CreateInputOptionPage(wpSelectDir,
    'Hardware runtime', 'Choose the PyTorch runtime for this computer.',
    'Select the backend that matches the primary processing device.', True, False);
  BackendPage.Add('NVIDIA CUDA 13.0 (RTX and newer GPUs)');
  BackendPage.Add('NVIDIA CUDA 12.6 (GTX and older GPUs)');
  BackendPage.Add('Intel Arc/Xe (XPU)');
  BackendPage.Add('CPU only (slow)');
  BackendPage.SelectedValueIndex := 0;

  ModePage := CreateInputOptionPage(BackendPage.ID,
    'Model installation', 'Choose how model checkpoints are installed.',
    'Standard is fastest. Missing models can download automatically when first requested.', True, False);
  ModePage.Add('Standard - SAM2 Base now; other models on demand');
  ModePage.Add('Complete - pre-download every supported model');
  ModePage.Add('Custom - choose individual model packs');
  ModePage.SelectedValueIndex := 0;

  CustomPage := CreateInputOptionPage(ModePage.ID,
    'Custom model packs', 'Choose model packs to pre-download.',
    'Already verified model files are kept and skipped.', False, True);
  CustomPage.Add('SAM2 Base (recommended)');
  CustomPage.Add('SAM2 Large');
  CustomPage.Add('EfficientTAM');
  CustomPage.Add('MatAnyone (noncommercial terms)');
  CustomPage.Add('MatAnyone2 (noncommercial terms)');
  CustomPage.Add('VideoMaMa (restricted and separate SVD VAE terms)');
  CustomPage.Add('MiniMax Remover (noncommercial terms)');
  CustomPage.Values[0] := True;

  LicensePage := CreateInputOptionPage(CustomPage.ID,
    'Optional model licenses', 'Restricted model packs require explicit acceptance.',
    'MatAnyone/MatAnyone2 use S-Lab noncommercial terms. VideoMaMa uses CC BY-NC 4.0 and its SVD VAE dependency has separate Stability AI Community License terms. MiniMax Remover has noncommercial terms. See THIRD_PARTY_NOTICES.md.', False, True);
  LicensePage.Add('I understand these model packs are not licensed for unrestricted commercial use.');
end;

function RestrictedSelected: Boolean;
begin
  Result := (ModePage.SelectedValueIndex = 1) or
    ((ModePage.SelectedValueIndex = 2) and
      (CustomPage.Values[3] or CustomPage.Values[4] or CustomPage.Values[5] or CustomPage.Values[6]));
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := False;
  if (PageID = CustomPage.ID) and (ModePage.SelectedValueIndex <> 2) then
    Result := True
  else if (PageID = LicensePage.ID) and not RestrictedSelected then
    Result := True;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  I: Integer;
  AnySelected: Boolean;
begin
  Result := True;
  if (CurPageID = CustomPage.ID) then
  begin
    AnySelected := False;
    for I := 0 to 6 do
      AnySelected := AnySelected or CustomPage.Values[I];
    if not AnySelected then
    begin
      MsgBox('Select at least one model pack.', mbError, MB_OK);
      Result := False;
    end;
  end;
  if (CurPageID = LicensePage.ID) and not LicensePage.Values[0] then
  begin
    MsgBox('Accept the displayed model restrictions or return and choose only unrestricted segmentation packs.', mbError, MB_OK);
    Result := False;
  end;
end;

function GetBackend(Param: String): String;
begin
  case BackendPage.SelectedValueIndex of
    0: Result := 'cu130';
    1: Result := 'cu126';
    2: Result := 'xpu';
  else
    Result := 'cpu';
  end;
end;

function GetInstallMode(Param: String): String;
begin
  case ModePage.SelectedValueIndex of
    1: Result := 'Complete';
    2: Result := 'Custom';
  else
    Result := 'Standard';
  end;
end;

procedure AddKey(var Value: String; Key: String);
begin
  if Value <> '' then
    Value := Value + ',';
  Value := Value + Key;
end;

function GetModelKeys(Param: String): String;
begin
  Result := '';
  if ModePage.SelectedValueIndex = 0 then
    Result := 'Base'
  else if ModePage.SelectedValueIndex = 1 then
    Result := 'all'
  else
  begin
    if CustomPage.Values[0] then AddKey(Result, 'Base');
    if CustomPage.Values[1] then AddKey(Result, 'Large');
    if CustomPage.Values[2] then AddKey(Result, 'Efficient');
    if CustomPage.Values[3] then AddKey(Result, 'matanyone');
    if CustomPage.Values[4] then AddKey(Result, 'matanyone2');
    if CustomPage.Values[5] then
    begin
      AddKey(Result, 'videomama');
      AddKey(Result, 'svd_vae');
    end;
    if CustomPage.Values[6] then
    begin
      AddKey(Result, 'minimax_transformer');
      AddKey(Result, 'minimax_vae');
    end;
  end;
end;

function GetLicenseFlag(Param: String): String;
begin
  if RestrictedSelected and LicensePage.Values[0] then
    Result := '-AcceptRestrictedModels'
  else
    Result := '';
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
  PowerShell: String;
  Parameters: String;
begin
  if CurStep <> ssPostInstall then
    Exit;

  WizardForm.StatusLabel.Caption := 'Installing the Samosa runtime and selected models. This can take several minutes...';
  PowerShell := ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe');
  Parameters := '-NoProfile -ExecutionPolicy Bypass -File "' + ExpandConstant('{app}\installer\bootstrap.ps1') +
    '" -InstallRoot "' + ExpandConstant('{app}') + '" -Backend "' + GetBackend('') +
    '" -InstallMode "' + GetInstallMode('') + '" -Models "' + GetModelKeys('') + '" ' + GetLicenseFlag('');

  if not Exec(PowerShell, Parameters, '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
    RaiseException('Could not start the Samosa runtime installer.');
  if ResultCode <> 0 then
    RaiseException('Samosa runtime installation failed. Review ' + ExpandConstant('{app}\logs\installer.log') + '.');
end;
