; 数据隐站 · Windows 安装包（Inno Setup）
; 简化版：不再在安装阶段静默安装 WebView2/VC++（避免卡住/无响应）。
; 程序已带 VC++ DLL；缺 WebView2 时主程序自动用默认浏览器打开，功能不受影响。
;
; 编译：iscc desktop\installer.iss
#define MyAppName "数据隐站"
#define MyAppNameEn "Sanitized_Data"
#define MyAppVersion "1.3.3"
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
PrivilegesRequired=lowest
OutputDir=..\dist_installer
OutputBaseFilename={#MyAppNameEn}_Setup_{#MyAppVersion}_x64
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
SetupIconFile=assets\app.ico
UninstallDisplayIcon={app}\app.ico
UninstallDisplayName={#MyAppName}
SetupLogging=yes

[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务："; Flags: unchecked

[Files]
Source: "..\dist\{#MyAppNameEn}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "assets\app.ico"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "立即启动{#MyAppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}\logs"
