Option Explicit
Dim shell, files, script, command
Set shell = CreateObject("WScript.Shell")
Set files = CreateObject("Scripting.FileSystemObject")
script = files.BuildPath(files.GetParentFolderName(WScript.ScriptFullName), "server-switch.ps1")
command = Chr(34) & shell.ExpandEnvironmentStrings("%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe") & Chr(34) & " -NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -File " & Chr(34) & script & Chr(34)
If WScript.Arguments.Count > 0 Then
    If WScript.Arguments.Count <> 1 Then WScript.Quit 2
    If WScript.Arguments(0) <> "Status" Then WScript.Quit 2
    command = command & " -Mode Status -NoPopup"
End If
WScript.Quit shell.Run(command, 0, True)
