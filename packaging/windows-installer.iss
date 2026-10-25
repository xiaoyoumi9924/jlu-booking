; JLU Booking per-user Windows installer.
;
; Build with Inno Setup 6:
;   ISCC.exe /DMyAppVersion=2.6.1 packaging\windows-installer.iss
;
; The resulting installer lives in dist\JLU-Booking-Setup.exe and creates a
; desktop shortcut unless the user clears the task on the install page.

#define MyAppName "JLU Booking"
#define MyAppDisplayName "吉林大学体育场馆预约助手"
#define MyAppPublisher "JLU Booking contributors"
#define MyAppURL "https://github.com/xiaoyoumi9924/jlu-booking"
#define MyAppExeName "JLU Booking.exe"

#ifndef MyAppVersion
  #define MyAppVersion "2.6.1"
#endif

[Setup]
AppId={{8C0B4C2A-0F1D-4B7E-9E11-6B7F5D2A9C31}
AppName={#MyAppDisplayName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppDisplayName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
AppUpdatesURL={#MyAppURL}/releases/latest
DefaultDirName={localappdata}\Programs\JLU Booking
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=..\dist
OutputBaseFilename=JLU-Booking-Setup
SetupIconFile=..\assets\JLU_LOGO.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppDisplayName}
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
SetupLogging=yes
CloseApplications=yes
RestartApplications=no
VersionInfoVersion={#MyAppVersion}.0
VersionInfoProductName={#MyAppDisplayName}
VersionInfoProductVersion={#MyAppVersion}.0
VersionInfoDescription={#MyAppDisplayName} 安装程序
VersionInfoCompany={#MyAppPublisher}
LicenseFile=..\LICENSE

[Languages]
Name: "chinesesimp"; MessagesFile: "ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务："; Flags: checkedonce

[Files]
Source: "..\dist\JLU Booking\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\{#MyAppExeName}"; Comment: "{#MyAppDisplayName}"; Tasks: desktopicon
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\{#MyAppExeName}"; Comment: "{#MyAppDisplayName}"
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "立即启动 {#MyAppDisplayName}"; Flags: nowait postinstall skipifsilent
