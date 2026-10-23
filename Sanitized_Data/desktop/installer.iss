; 数据隐站 · Windows 安装包（Inno Setup 6）
; 编译：iscc desktop\installer.iss
;
; 设计要点（面向非技术用户）：
;   1. 不需要用户自己装 Python —— 运行时已打包进程序
;   2. 缺 WebView2 运行时 → 安装时自动静默安装
;   3. 缺 VC++ 运行库 → 安装时自动静默安装（另有一层兜底：打包时已带上 DLL）
;   4. 安装前给出一页「环境检查」报告，让用户知道这台机器差什么
;   5. 装到 %LOCALAPPDATA%，不需要管理员权限

#define MyAppName "数据隐站"
#define MyAppNameEn "Sanitized_Data"
#define MyAppVersion "1.2.1"
#define MyAppPublisher "BLCTEK"
#define MyAppExeName "Sanitized_Data.exe"

[Setup]
AppId={{9C4E2B71-3D5A-4F82-9E61-7A0C5B8D4E13}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\{#MyAppNameEn}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
; 不需要管理员：装到用户目录
PrivilegesRequired=lowest
OutputDir=..\dist_installer
OutputBaseFilename={#MyAppNameEn}_Setup_{#MyAppVersion}_x64
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
; 安装包自身的图标（资源管理器里的 setup.exe / 安装向导），相对本 .iss 所在目录；
; 唯一图源 desktop/assets/app.svg，派生 app.ico 见 docs/research/06-图标适配检查与替换记录.md
SetupIconFile=assets\app.ico
; 「应用和功能」列表里的卸载图标：用 [Files] 复制到 {app}\app.ico 的同一份文件
UninstallDisplayIcon={app}\app.ico
UninstallDisplayName={#MyAppName}

[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务："; Flags: unchecked

[Files]
Source: "..\dist\{#MyAppNameEn}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; 运行时安装包：先不落地，环境检查后按需解压到临时目录
Source: "redist\MicrosoftEdgeWebview2Setup.exe"; Flags: dontcopy
Source: "redist\VC_redist.x64.exe"; Flags: dontcopy
; 项目图标：复制到 {app}\app.ico，供 UninstallDisplayIcon 使用
Source: "assets\app.ico"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "立即启动{#MyAppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}\logs"

[Code]

const
  WEBVIEW2_GUID = '{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';
  VC_RUNTIME_KEY = 'SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64';

var
  EnvShown: Boolean;
  HasWebView2: Boolean;
  HasVCRedist: Boolean;
  OsName: String;

{ ───────── 环境探测 ───────── }

function GetWebView2Version(): String;
var v: String;
begin
  Result := '';
  if RegQueryStringValue(HKLM64, 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\' + WEBVIEW2_GUID, 'pv', v) then
    Result := v
  else if RegQueryStringValue(HKLM, 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\' + WEBVIEW2_GUID, 'pv', v) then
    Result := v
  else if RegQueryStringValue(HKCU, 'Software\Microsoft\EdgeUpdate\Clients\' + WEBVIEW2_GUID, 'pv', v) then
    Result := v;
  { 0.0.0.0 表示登记了但运行时没装 }
  if Result = '0.0.0.0' then
    Result := '';
end;

function GetVCRedistInstalled(): Boolean;
var ok: Cardinal;
begin
  Result := False;
  if RegQueryDWordValue(HKLM, VC_RUNTIME_KEY, 'Installed', ok) then
    Result := ok = 1;
end;

function DetectOSName(): String;
begin
  Result := 'Windows';
  if IsWin64 then
    Result := Result + ' 64 位';
end;

function BytesToMB(B: Int64): String;
begin
  Result := IntToStr(B div (1024 * 1024)) + ' MB';
end;

{ ───────── 环境检查页 ───────── }

function BuildEnvReport(): String;
var
  s: String;
  freeMB, totalMB: Cardinal;
begin
  s := '';
  s := s + '系统：' + OsName + #13#10;
  s := s + '安装位置：' + ExpandConstant('{localappdata}\Programs\{#MyAppNameEn}') + #13#10;
  s := s + #13#10 + '【程序依赖】' + #13#10;

  if HasWebView2 then
    s := s + '  ✓ 窗口组件 WebView2 —— 已安装'
  else
    s := s + '  ! 窗口组件 WebView2 —— 缺失，安装时自动补齐' + #13#10 +
         '    （若补齐失败，程序会自动改用默认浏览器打开，功能不受影响）';
  s := s + #13#10;

  if HasVCRedist then
    s := s + '  ✓ 运行库 VC++ 2015-2022 —— 已安装'
  else
    s := s + '  ! 运行库 VC++ 2015-2022 —— 缺失，安装时自动补齐';

  s := s + #13#10 + #13#10 + '【说明】' + #13#10 +
       '  本程序自带 Python 运行环境，不需要你另外安装任何东西。' + #13#10 +
       '  全部数据只保存在这台电脑上，不会联网上传。';

  if GetSpaceOnDisk(ExtractFileDrive(ExpandConstant('{localappdata}')), True, freeMB, totalMB) then
    s := s + #13#10 + #13#10 + '  可用磁盘空间：' + IntToStr(freeMB) + ' MB';

  Result := s;
end;

procedure InitializeWizard();
begin
  HasWebView2 := GetWebView2Version() <> '';
  HasVCRedist := GetVCRedistInstalled();
  OsName := DetectOSName();
  EnvShown := False;
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  { 进到欢迎页时先给一份环境检查结果：缺什么、由谁补，写清楚 }
  if (CurPageID = wpWelcome) and (not EnvShown) then
  begin
    EnvShown := True;
    if not WizardSilent then      { 静默安装 /SILENT 时不堵弹窗 }
      MsgBox('环境检查结果：' + #13#10#13#10 + BuildEnvReport(), mbInformation, MB_OK);
  end;
end;

{ ───────── 自动补齐环境 ───────── }

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  rc: Integer;
  ExePath: String;
begin
  Result := '';

  { VC++ 运行库 }
  if not GetVCRedistInstalled() then
  begin
    WizardForm.StatusLabel.Caption := '正在安装 Microsoft Visual C++ 运行库…';
    ExtractTemporaryFile('VC_redist.x64.exe');
    ExePath := ExpandConstant('{tmp}\VC_redist.x64.exe');
    if FileExists(ExePath) then
      Exec(ExePath, '/quiet /norestart', '', SW_HIDE, ewWaitUntilTerminated, rc);
  end;

  { WebView2 运行时 }
  if GetWebView2Version() = '' then
  begin
    WizardForm.StatusLabel.Caption := '正在安装 WebView2 窗口组件…';
    ExtractTemporaryFile('MicrosoftEdgeWebview2Setup.exe');
    ExePath := ExpandConstant('{tmp}\MicrosoftEdgeWebview2Setup.exe');
    if FileExists(ExePath) then
      Exec(ExePath, '/silent /install', '', SW_HIDE, ewWaitUntilTerminated, rc);
  end;

  { 装不上不算失败：程序有浏览器兜底 }
  NeedsRestart := False;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Msg: String;
begin
  if CurStep = ssPostInstall then
  begin
    if (GetWebView2Version() = '') and (GetVCRedistInstalled() = False) then
      Msg := '两个运行组件都没能自动装好。程序仍然可以运行，会用默认浏览器打开界面。' + #13#10#13#10 +
             '如果之后想用独立窗口，可以手动安装：' + #13#10 +
             'https://developer.microsoft.com/microsoft-edge/webview2/'
    else if GetWebView2Version() = '' then
      Msg := '窗口组件 WebView2 没装上，程序会自动用默认浏览器打开界面，功能完全一样。'
    else
      Msg := '';

    if Msg <> '' then
      MsgBox(Msg, mbInformation, MB_OK);
  end;
end;
