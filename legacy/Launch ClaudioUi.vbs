' ClaudioUi Launcher

Dim fso, shell, dir, psScript
Set fso   = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
dir      = fso.GetParentFolderName(WScript.ScriptFullName)
psScript = dir & "\claudioui_server.ps1"

' Kill any process on port 8765
On Error Resume Next
Dim out, lines, i, parts, pid
out = shell.Exec("cmd /c netstat -aon 2>nul | findstr "":8765 """).StdOut.ReadAll()
lines = Split(out, vbNewLine)
For i = 0 To UBound(lines)
    parts = Split(Trim(lines(i)))
    If UBound(parts) >= 4 Then
        pid = parts(UBound(parts))
        If IsNumeric(pid) And CLng(pid) > 0 Then
            shell.Run "taskkill /PID " & pid & " /F", 0, True
        End If
    End If
Next
On Error GoTo 0

WScript.Sleep 1200

' Start server hidden
shell.Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & psScript & """", 0, False

' Wait up to 8 seconds for server to be ready, then open browser
Dim tries
For tries = 1 To 16
    WScript.Sleep 500
    On Error Resume Next
    Dim http
    Set http = CreateObject("MSXML2.XMLHTTP")
    http.Open "GET", "http://localhost:8765/api/state", False
    http.Send
    If Err.Number = 0 And http.Status = 200 Then
        shell.Run "http://localhost:8765"
        WScript.Quit
    End If
    On Error GoTo 0
Next

' If we get here the server didn't start — show error
MsgBox "ClaudioUi server failed to start." & vbNewLine & _
       "Check " & dir & "\claudioui.log for details.", _
       vbExclamation, "ClaudioUi"
